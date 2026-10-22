"""
Subtitles in a copy of the recording's video (the original file is never modified).

  * soft subtitles: the video and audio streams are copied as they are (-c copy) and a subtitle
    track with a language tag is added (mov_text in MP4/MOV, SRT in MKV). Takes seconds; players
    can switch the track on and off.
  * burned-in subtitles: the text is drawn into the picture with FFmpeg's `subtitles` filter
    (libass), so the video is re-encoded (libx264). Slow; runs as a queue job with progress and cancel.
"""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.ffmpeg_manager import tool_cmd
from app.paths import DATA_DIR, EXPORTS_DIR
from app.translation import ISO639_2

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
_MP4_LIKE = {".mp4", ".m4v", ".mov"}
# Work files stay in the data folder: under a Microsoft Store Python, files written to %TEMP%
# (inside %LOCALAPPDATA%) may be virtualized and invisible to FFmpeg
_WORK_DIR = DATA_DIR / "tmp"

# Readable on dark and bright footage; libass scales FontSize to a 288-line canvas for SRT input
BURN_STYLE = "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1.2,Shadow=0,MarginV=18"


def probe_media(path: Path) -> Dict[str, Any]:
    proc = subprocess.run(
        [tool_cmd("ffprobe"), "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name:stream_disposition=attached_pic:format=duration",
         "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    try:
        info = json.loads(proc.stdout or "{}")
    except Exception:
        info = {}
    streams = info.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    try:
        duration = float((info.get("format") or {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    return {
        "has_video": bool(video),
        "video_codec": video[0].get("codec_name") if video else "",
        "audio_codecs": [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"],
        "duration": duration,
    }


def safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title or "").strip(" .") or "recording"
    return name[:80]


def output_extension(media: Path) -> str:
    """MP4-family sources stay MP4 (mov_text subtitles); everything else goes to MKV."""
    if media.suffix.lower() in _MP4_LIKE:
        return ".mp4"
    return ".mkv"


def default_output_path(title: str, media: Path, lang: str, variant: str, kind: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"{lang}+orig" if variant == "bilingual" else lang
    label = "subs" if kind == "soft" else "hardsub"
    return EXPORTS_DIR / f"{safe_name(title)}.{tag}.{label}.{stamp}{output_extension(media)}"


def check_output_path(media: Path, out_path: Path) -> None:
    if out_path.resolve() == media.resolve():
        raise ValueError("Нельзя сохранить результат поверх исходного файла")


def _run_ffmpeg(args: List[str], cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(
        [tool_cmd("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW, cwd=str(cwd) if cwd else None,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg: {proc.stderr[-600:]}")


def _subtitle_tags(lang: str, title: str) -> List[str]:
    tags = ["-metadata:s:s:0", f"title={title}", "-metadata:s:s:0", f"handler_name={title}"]
    if lang in ISO639_2:
        tags += ["-metadata:s:s:0", f"language={ISO639_2[lang]}"]
    return tags


def mux_soft_subtitles(media: Path, srt_text: str, lang: str, track_title: str, out_path: Path) -> Path:
    """Copies video/audio streams and adds one subtitle track. Returns the output path."""
    check_output_path(media, out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sub_codec = "mov_text" if out_path.suffix.lower() in _MP4_LIKE else "srt"
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="subs-", dir=_WORK_DIR) as tmp:
        srt = Path(tmp) / "subs.srt"
        srt.write_text(srt_text, encoding="utf-8")
        partial = out_path.with_name(out_path.stem + ".part" + out_path.suffix)
        args = [
            "-i", str(media), "-sub_charenc", "UTF-8", "-i", str(srt),
            "-map", "0:v", "-map", "0:a?", "-map", "1:0",
            "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
            *_subtitle_tags(lang, track_title), "-disposition:s:0", "default",
        ]
        if out_path.suffix.lower() == ".mp4":
            args += ["-movflags", "+faststart"]
        try:
            _run_ffmpeg(args + [str(partial)])
            partial.replace(out_path)
        finally:
            partial.unlink(missing_ok=True)
    return out_path


def _pick_video_encoder() -> List[str]:
    try:
        proc = subprocess.run([tool_cmd("ffmpeg"), "-hide_banner", "-encoders"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                              creationflags=_NO_WINDOW)
        if " libx264 " in proc.stdout:
            return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    except Exception:
        pass
    return []  # FFmpeg's default encoder for the container


def burn_subtitles(
    media: Path,
    srt_text: str,
    out_path: Path,
    duration: float,
    progress: Callable[[int, str], None],
    check_cancel: Callable[[], None],
) -> Path:
    """Re-encodes the video with the subtitles drawn into the picture. Cancelling kills FFmpeg."""
    check_output_path(media, out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(out_path.stem + ".part" + out_path.suffix)
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="burn-", dir=_WORK_DIR))
    try:
        (tmp / "subs.srt").write_text(srt_text, encoding="utf-8")
        # The filter gets a relative file name (FFmpeg runs inside the temp folder): Windows paths
        # with a drive colon need several layers of escaping inside a filter graph.
        vf = f"subtitles=subs.srt:charenc=UTF-8:force_style='{BURN_STYLE}'"
        args = [
            tool_cmd("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
            "-i", str(media), "-map", "0:v:0", "-map", "0:a?", "-vf", vf, *_pick_video_encoder(),
            "-c:a", "copy",
        ]
        if out_path.suffix.lower() == ".mp4":
            args += ["-movflags", "+faststart"]
        args.append(str(partial))
        from app.shutdown import track

        proc = track(subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", creationflags=_NO_WINDOW, cwd=str(tmp),
        ))
        err_tail: List[str] = []

        def _drain_err() -> None:
            for line in proc.stderr:
                err_tail.append(line.rstrip())
                del err_tail[:-20]

        threading.Thread(target=_drain_err, daemon=True).start()
        cancelled = {"flag": False}

        def _watch() -> None:
            while proc.poll() is None:
                try:
                    check_cancel()
                except Exception:
                    cancelled["flag"] = True
                    proc.kill()
                    return
                time.sleep(0.3)

        threading.Thread(target=_watch, daemon=True).start()
        total = max(1.0, float(duration or 0.0))
        last_pct = -1
        t0 = time.time()
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit():
                sec = int(value) / 1_000_000
                pct = max(1, min(99, int(sec / total * 100)))
                if pct != last_pct:
                    last_pct = pct
                    speed = sec / max(0.1, time.time() - t0)
                    progress(pct, f"Встраивание субтитров (перекодирование видео, {speed:.1f}x): {pct}%")
        proc.wait()
        if cancelled["flag"]:
            check_cancel()  # raises the job's cancel exception
        if proc.returncode != 0:
            raise RuntimeError("FFmpeg: " + "\n".join(err_tail[-8:]))
        partial.replace(out_path)
        return out_path
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
