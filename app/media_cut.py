"""
Cutting a recording into a shorter file (FFmpeg trim + concat):

- "clean": only the speech, from the word timings of the current transcript. Words removed by the
  filler cleanup are not there any more, so they are cut out too; a pause longer than MAX_GAP_SEC
  is shortened to a short breath.
- "highlights": the key moments of the recording (decisions, tasks, conclusions) picked by the
  analysis model from the transcript, each a few tens of seconds, joined into a short reel.

The original file is never changed; video is re-encoded (cuts must land on any frame).
"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Interval = Tuple[float, float]

MAX_GAP_SEC = 0.6     # a longer silence between words is cut (down to 2 * PAD_SEC)
PAD_SEC = 0.12        # kept around speech so words do not start or end abruptly
HIGHLIGHT_MIN_SEC, HIGHLIGHT_MAX_SEC = 8.0, 75.0
# "Clean sound": hum and rumble cut, steady noise removed (FFmpeg's own FFT denoiser, no model),
# speech levelled. Mild settings: voices must not sound processed.
DENOISE_FILTER = "highpass=f=70,lowpass=f=12000,afftdn=nr=12:nf=-30:tn=1,dynaudnorm=f=250:g=15:p=0.9"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def merge(intervals: List[Interval], join_gap: float = 0.0) -> List[Interval]:
    out: List[Interval] = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1] + join_gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def speech_intervals(segments: List[Dict[str, Any]], duration: float) -> List[Interval]:
    """What to keep for the "clean" cut: words (or whole segments without word timings), padded,
    with the gaps between them shorter than MAX_GAP_SEC kept as they are."""
    raw: List[Interval] = []
    for s in segments:
        words = [w for w in s.get("words") or [] if str(w.get("word") or "").strip()]
        if words:
            raw.extend((float(w.get("start") or 0), float(w.get("end") or 0)) for w in words)
        elif (s.get("text") or "").strip():
            raw.append((float(s.get("start") or 0), float(s.get("end") or 0)))
    spans = merge(raw, join_gap=MAX_GAP_SEC)
    return merge([(max(0.0, a - PAD_SEC), min(duration or b + PAD_SEC, b + PAD_SEC)) for a, b in spans])


def kept_seconds(intervals: List[Interval]) -> float:
    return sum(b - a for a, b in intervals)


_MOMENT_RE = re.compile(r"(\d{1,2}:)?(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}:)?(\d{1,2}):(\d{2})\s*[|:\-–—]?\s*(.*)")


def _sec(h: Optional[str], m: str, s: str) -> float:
    return (int(h[:-1]) * 3600 if h else 0) + int(m) * 60 + int(s)


def parse_moments(answer: str, duration: float) -> List[Dict[str, Any]]:
    """Lines "MM:SS-MM:SS | title" (or with hours) of the model's answer."""
    out = []
    for line in (answer or "").splitlines():
        m = _MOMENT_RE.search(line)
        if not m:
            continue
        a, b = _sec(m.group(1), m.group(2), m.group(3)), _sec(m.group(4), m.group(5), m.group(6))
        if b < a:
            a, b = b, a
        if b - a < HIGHLIGHT_MIN_SEC:
            b = a + HIGHLIGHT_MIN_SEC
        b = min(b, a + HIGHLIGHT_MAX_SEC, duration or b)
        if a < (duration or a + 1):
            out.append({"start": float(a), "end": float(b), "title": m.group(7).strip(" *-–—|")[:120]})
    return out


def pick_highlights(m: Dict[str, Any], model_name: Optional[str] = None, answer_lang: str = "ru") -> List[Dict[str, Any]]:
    from app.database import is_local_only
    from app.llm import target
    from app.ollama_client import _lang_phrase, build_transcript_lines

    lines = build_transcript_lines(m.get("segments") or [], m.get("speakers") or {})
    if not lines:
        raise ValueError("Нет текста записи")
    llm = target(model_name, local_only=is_local_only(m))
    text = "\n".join(lines)[: max(8000, llm.max_chars - 3000)]
    system = (
        "Ты выбираешь ключевые моменты записи встречи для короткой нарезки: решения, договорённости, задачи, "
        "важные выводы и ответы. Выбери от 3 до 8 моментов, каждый 15–60 секунд, в хронологическом порядке. "
        "Ответ — только строки вида «MM:SS-MM:SS | короткое название момента» без пояснений. "
        f"Названия пиши {_lang_phrase(answer_lang)}."
    )
    moments = parse_moments(llm.chat(system, "Расшифровка с отметками времени:\n" + text), float(m.get("duration") or 0))
    if not moments:
        raise RuntimeError("Модель не нашла ключевых моментов")
    return moments


def cut_command(ffmpeg: str, media: Path, intervals: List[Interval], has_video: bool, out: Path,
                script: Path, afilter: str = "") -> List[str]:
    """FFmpeg command that joins `intervals` of `media`; the filter goes to a script file (a long
    recording has hundreds of pieces, too many for a command line)."""
    parts, labels = [], []
    for i, (a, b) in enumerate(intervals):
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS{',' + afilter if afilter else ''}[a{i}]")
        if has_video:
            parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
            labels.append(f"[v{i}][a{i}]")
        else:
            labels.append(f"[a{i}]")
    n = len(intervals)
    parts.append("".join(labels) + (f"concat=n={n}:v=1:a=1[v][a]" if has_video else f"concat=n={n}:v=0:a=1[a]"))
    script.write_text(";\n".join(parts), encoding="utf-8")
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
           "-i", str(media), "-filter_complex_script", str(script)]
    if has_video:
        cmd += ["-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-map", "[a]"]
    cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    return cmd


def run_cut(ffmpeg: str, media: Path, intervals: List[Interval], has_video: bool, out: Path,
            on_progress: Callable[[float], None], check_cancel: Callable[[], None], afilter: str = "") -> Path:
    if not intervals:
        raise ValueError("Нечего оставить: в записи не найдено речи")
    total = max(0.1, kept_seconds(intervals))
    with tempfile.TemporaryDirectory() as tmp:
        cmd = cut_command(ffmpeg, media, intervals, has_video, out, Path(tmp) / "cut.txt", afilter)
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace", creationflags=_NO_WINDOW) as proc:
            _follow(proc, total, out, on_progress, check_cancel)
    return out


def _follow(proc: subprocess.Popen, total: float, out: Path, on_progress: Callable[[float], None],
            check_cancel: Callable[[], None]) -> None:
    """Reports FFmpeg's progress (-progress pipe:1) and stops it on a cancel."""
    try:
        for line in proc.stdout:
            check_cancel()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    on_progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / total))
                except ValueError:
                    pass
        proc.wait()
    except BaseException:
        proc.kill()
        out.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg: " + (proc.stderr.read() or "")[-300:])
