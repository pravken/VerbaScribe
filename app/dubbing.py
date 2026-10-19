"""
Translate & Dub (prototype).

Pipeline for a completed recording:
  1. split diarized segments into short phrase units using word timestamps
  2. translate the units with the local Ollama model, asking for a similar spoken length
  3. separate voice and background with Demucs                     (GPU worker, .venv-dub)
  4. synthesize every unit in the original speaker's voice with
     Chatterbox Multilingual, using a reference cut from the clean vocals (GPU worker)
  5. re-translate units that came out far too long, then time-fit each clip to its slot (FFmpeg atempo)
  6. mix the dubbed voice over the background and mux it into a new, clearly labelled file

The heavy models live in a separate process (app/dub_worker.py running in .venv-dub): their pinned
library versions conflict with the main app, and killing the process is the cleanest way to cancel a
job and give all VRAM back.
"""

import json
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import numpy as np
import soundfile as sf

from app.audio_utils import format_timestamp, get_media_duration
from app.database import get_all_settings
from app.dub_text import build_dub_units
from app.ollama_client import ensure_ollama_running, get_ollama_status
from app.paths import APP_DIR, DATA_DIR, MODELS_DIR

# The optional environment sits next to the program; outputs go to the per-user data folder
DUB_PYTHON = APP_DIR / ".venv-dub" / "Scripts" / "python.exe"
DUB_WORKER = Path(__file__).resolve().parent / "dub_worker.py"
DUBS_DIR = DATA_DIR / "dubs"

# Languages supported by Chatterbox Multilingual (code -> English name used in LLM prompts)
DUB_LANGUAGES: Dict[str, str] = {
    "ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch",
    "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "sv": "Swedish",
    "sw": "Swahili", "tr": "Turkish", "zh": "Chinese",
}

MIX_SR = 44100
REF_TARGET_SEC = 10.0    # length of the voice reference cut per speaker
TEMPO_RETRANSLATE = 1.35 # clips needing more speed-up than this are re-translated shorter once
TEMPO_MAX = 1.6          # hard cap for speed-up; whatever is left is trimmed with a fade

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], None]


def dub_environment_status() -> Dict[str, Any]:
    return {
        "installed": DUB_PYTHON.exists(),
        "python": str(DUB_PYTHON),
        "languages": list(DUB_LANGUAGES.keys()),
    }


# ---------------------------------------------------------------- 2. translation (Ollama)
def _ollama_chat(base_url: str, model: str, system: str, user: str, keep_alive: str = "5m") -> str:
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "stream": False,
                "keep_alive": keep_alive,
                "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 8192},
            },
        )
        resp.raise_for_status()
        return str((resp.json().get("message") or {}).get("content") or "").strip()


def _unload_ollama(base_url: str, model: str) -> None:
    """Frees the LLM's VRAM before the TTS model is loaded."""
    try:
        with httpx.Client(timeout=30.0) as client:
            client.post(f"{base_url}/api/generate", json={"model": model, "keep_alive": 0})
    except Exception:
        pass


_LINE_RE = re.compile(r"^\s*(\d+)\s*[.):]\s*(.+?)\s*$")


def _translate_batch(base_url: str, model: str, texts: List[str], src: str, tgt: str) -> List[Optional[str]]:
    system = (
        f"You are a professional dubbing translator from {src} to {tgt}. "
        "Each numbered line is one spoken phrase of a video. The translation will be voiced over the "
        "original timing, so keep every line about as long as the original when spoken (similar number "
        "of syllables), natural and conversational. Never merge, split, skip or explain lines. "
        f"Answer only in {tgt}, with exactly the same numbering, one line per phrase: `N. translation`."
    )
    user = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    out: List[Optional[str]] = [None] * len(texts)
    for line in _ollama_chat(base_url, model, system, user).splitlines():
        m = _LINE_RE.match(line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(texts) and out[idx] is None:
                out[idx] = m.group(2).strip().strip("`")
    return out


def _translate_single(base_url: str, model: str, text: str, src: str, tgt: str) -> str:
    system = (
        f"Translate the user's {src} phrase into natural spoken {tgt} of about the same length. "
        "Output only the translation, nothing else."
    )
    return _ollama_chat(base_url, model, system, text).strip().strip('"«»`')


def _shorten(base_url: str, model: str, source: str, current: str, max_chars: int, src: str, tgt: str) -> str:
    system = (
        f"You shorten dubbing lines. Rewrite the {tgt} line so it is at most {max_chars} characters long "
        f"and still conveys the meaning of the {src} original. Output only the new {tgt} line."
    )
    user = f"Original ({src}): {source}\nCurrent ({tgt}): {current}"
    res = _ollama_chat(base_url, model, system, user).strip().strip('"«»`')
    return res if 0 < len(res) < len(current) else current


def translate_units(
    units: List[Dict[str, Any]],
    src_code: str,
    tgt_code: str,
    progress: ProgressFn,
    check_cancel: CancelFn,
    batch_size: int = 12,
) -> None:
    settings = get_all_settings()
    base_url = (settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
    model = settings.get("ollama_model") or "qwen2.5:7b"
    ensure_ollama_running(base_url)
    st = get_ollama_status(base_url)
    if not st["running"] or not st["models"]:
        raise RuntimeError("Ollama не запущена или в ней нет моделей — перевод невозможен")
    if model not in st["models"]:
        model = st["models"][0]
    src = DUB_LANGUAGES.get(src_code, src_code)
    tgt = DUB_LANGUAGES[tgt_code]

    for b in range(0, len(units), batch_size):
        check_cancel()
        chunk = units[b : b + batch_size]
        res = _translate_batch(base_url, model, [u["text"] for u in chunk], src, tgt)
        for u, t in zip(chunk, res):
            u["translation"] = t if t else _translate_single(base_url, model, u["text"], src, tgt)
        progress(5 + int(10 * min(1.0, (b + batch_size) / max(1, len(units)))), f"Перевод: {min(len(units), b + batch_size)} из {len(units)} фраз")


# ---------------------------------------------------------------- GPU worker process
class DubWorker:
    """Talks to app/dub_worker.py in .venv-dub. Cancelling kills the process (and frees VRAM)."""

    def __init__(self, check_cancel: CancelFn):
        if not DUB_PYTHON.exists():
            raise RuntimeError("Окружение дубляжа не установлено. Запустите setup_dub.bat")
        env = {
            **__import__("os").environ,
            "HF_HOME": str(MODELS_DIR / "dub" / "hf"),
            "TORCH_HOME": str(MODELS_DIR / "dub" / "torch"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        self._check_cancel = check_cancel
        self._cancelled = False
        self._stderr: deque = deque(maxlen=60)
        self.proc = subprocess.Popen(
            [str(DUB_PYTHON), "-I", str(DUB_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=_NO_WINDOW,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._stop = threading.Event()
        threading.Thread(target=self._watch_cancel, daemon=True).start()
        self._read()  # "ready"

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())

    def _watch_cancel(self) -> None:
        while not self._stop.wait(0.3):
            try:
                self._check_cancel()
            except Exception:
                self._cancelled = True
                self.proc.kill()
                return

    def _read(self) -> Dict[str, Any]:
        line = self.proc.stdout.readline()
        if not line:
            self.proc.wait(timeout=10)
            if self._cancelled:
                self._check_cancel()  # raises JobCancelled
            raise RuntimeError("Процесс дубляжа завершился неожиданно:\n" + "\n".join(list(self._stderr)[-15:]))
        msg = json.loads(line)
        if not msg.get("ok"):
            raise RuntimeError(f"Ошибка процесса дубляжа: {msg.get('error')}\n{msg.get('trace', '')}")
        return msg

    def call(self, req: Dict[str, Any], on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        while True:
            msg = self._read()
            if msg.get("event") == "progress":
                if on_progress:
                    on_progress(msg)
                continue
            return msg

    def close(self) -> None:
        self._stop.set()
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write('{"cmd": "quit"}\n')
                self.proc.stdin.flush()
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()


# ---------------------------------------------------------------- audio helpers
def _ffmpeg(args: List[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg: {proc.stderr[-600:]}")


def _has_video(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type,disposition=attached_pic",
         "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    try:
        streams = json.loads(proc.stdout or "{}").get("streams", [])
    except Exception:
        return False
    return any(not (s.get("disposition") or {}).get("attached_pic") for s in streams)


def _read_mono(path: Path, sr: int) -> np.ndarray:
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    if file_sr != sr:
        raise RuntimeError(f"{path.name}: {file_sr} Hz, ожидалось {sr} Hz")
    return data.mean(axis=1)


def _trim_silence(x: np.ndarray, sr: int, thresh_db: float = -42.0) -> np.ndarray:
    if x.size == 0:
        return x
    win = max(1, int(sr * 0.01))
    frames = x[: len(x) // win * win].reshape(-1, win)
    if frames.size == 0:
        return x
    rms_db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    active = np.where(rms_db > thresh_db)[0]
    if active.size == 0:
        return x
    a = max(0, (active[0] - 2) * win)
    b = min(len(x), (active[-1] + 3) * win)
    return x[a:b]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x ** 2).mean())) if x.size else 0.0


def _stretch_clip(src: Path, dst: Path, tempo: float, use_rubberband: bool) -> None:
    """Time-stretches a clip without changing pitch and resamples it to the mix rate."""
    filters = []
    if abs(tempo - 1.0) > 0.01:
        if use_rubberband:
            filters.append(f"rubberband=tempo={tempo:.4f}:pitchq=quality")
        else:
            t = tempo
            while t > 2.0:  # atempo accepts 0.5..2.0 per instance
                filters.append("atempo=2.0")
                t /= 2.0
            filters.append(f"atempo={t:.4f}")
    filters.append(f"aresample={MIX_SR}")
    _ffmpeg(["-i", str(src), "-af", ",".join(filters), "-ac", "1", "-c:a", "pcm_f32le", str(dst)])


def _ffmpeg_has_filter(name: str) -> bool:
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        return f" {name} " in proc.stdout
    except Exception:
        return False


def _build_speaker_refs(units: List[Dict[str, Any]], vocals: np.ndarray, out_dir: Path) -> Dict[str, Path]:
    """Cuts ~10 s of the speaker's clean (Demucs-separated) voice from their longest phrases."""
    refs: Dict[str, Path] = {}
    gap = np.zeros(int(0.15 * MIX_SR), dtype=np.float32)
    for spk in sorted({u["speaker"] for u in units}):
        parts, total = [], 0.0
        for u in sorted((u for u in units if u["speaker"] == spk), key=lambda u: u["end"] - u["start"], reverse=True):
            a, b = int(u["start"] * MIX_SR), int(min(u["end"], u["start"] + REF_TARGET_SEC + 2) * MIX_SR)
            piece = vocals[a:b]
            if piece.size < MIX_SR * 0.8:
                continue
            parts += [piece, gap]
            total += piece.size / MIX_SR
            if total >= REF_TARGET_SEC:
                break
        if not parts:
            continue
        ref = np.concatenate(parts)[: int((REF_TARGET_SEC + 2) * MIX_SR)]
        peak = float(np.abs(ref).max()) or 1.0
        path = out_dir / f"ref_{spk}.wav"
        sf.write(str(path), (ref / peak * 0.9).astype(np.float32), MIX_SR)
        refs[spk] = path
    return refs


# MP4/MKV store track languages as ISO 639-2 codes; two-letter codes are silently dropped
_ISO639_2 = {
    "ar": "ara", "da": "dan", "de": "deu", "el": "ell", "en": "eng", "es": "spa", "fi": "fin", "fr": "fra",
    "he": "heb", "hi": "hin", "it": "ita", "ja": "jpn", "ko": "kor", "ms": "msa", "nl": "nld", "no": "nor",
    "pl": "pol", "pt": "por", "ru": "rus", "sv": "swe", "sw": "swa", "tr": "tur", "zh": "zho", "uk": "ukr",
}


def _track_tags(index: int, lang: str, title: str) -> List[str]:
    spec = f"-metadata:s:a:{index}"
    tags = [spec, f"title={title}", spec, f"handler_name={title}"]  # MP4 players show handler_name
    if lang in _ISO639_2:
        tags += [spec, f"language={_ISO639_2[lang]}"]
    return tags


def _safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title).strip(" .") or "recording"
    return name[:80]


def _write_srt(units: List[Dict[str, Any]], path: Path) -> None:
    lines = []
    for i, u in enumerate(units, 1):
        lines += [str(i), f"{format_timestamp(u['start'], srt=True)} --> {format_timestamp(u['end'], srt=True)}",
                  u.get("translation") or "", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- main job
def run_dub_job(meeting: Dict[str, Any], target_lang: str, progress: ProgressFn, check_cancel: CancelFn) -> Dict[str, Any]:
    if target_lang not in DUB_LANGUAGES:
        raise ValueError(f"Язык {target_lang!r} не поддерживается для дубляжа")
    media = Path(meeting["media_path"])
    if not media.exists():
        raise RuntimeError(f"Исходный файл не найден: {media}")
    src_lang = meeting.get("language") or "ru"
    settings = get_all_settings()
    use_rubberband = settings.get("dub_stretch", "atempo") == "rubberband" and _ffmpeg_has_filter("rubberband")
    keep_original = settings.get("dub_keep_original_track", "true").lower() == "true"
    # Chatterbox tip: a reference in another language than the target transfers its accent; a lower
    # CFG weight reduces that at the cost of slightly less faithful pacing.
    cfg_weight = float(settings.get("dub_cfg_weight", "0.3"))
    exaggeration = float(settings.get("dub_exaggeration", "0.5"))

    work = DUBS_DIR / f"work_meeting_{meeting['id']}_{target_lang}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    (work / "tts").mkdir(parents=True, exist_ok=True)
    (work / "fit").mkdir(parents=True, exist_ok=True)
    stats: Dict[str, Any] = {"started": datetime.now().isoformat(timespec="seconds")}
    t_start = datetime.now()

    try:
        # 1. phrases + original audio
        progress(2, "Подготовка фраз и аудио...")
        units = build_dub_units(meeting.get("segments") or [])
        if not units:
            raise RuntimeError("В записи нет реплик для дубляжа")
        orig_wav = work / "original.wav"
        _ffmpeg(["-i", str(media), "-vn", "-ac", "2", "-ar", str(MIX_SR), "-c:a", "pcm_s16le", str(orig_wav)])
        total_dur = get_media_duration(orig_wav)

        # 2. translation (LLM is unloaded right after, so it never shares VRAM with the TTS)
        check_cancel()
        progress(5, f"Перевод {len(units)} фраз через Ollama...")
        translate_units(units, src_lang, target_lang, progress, check_cancel)
        # A phrase the LLM returned empty stays silent in the dub (the background still plays)
        units = [u for u in units if (u.get("translation") or "").strip()]
        if not units:
            raise RuntimeError("Модель Ollama не вернула перевод ни для одной фразы")
        settings_now = get_all_settings()
        ollama_url = (settings_now.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
        _unload_ollama(ollama_url, settings_now.get("ollama_model") or "")

        # 3. voice / background separation
        check_cancel()
        progress(16, "Отделение голоса от фона (Demucs)...")
        worker = DubWorker(check_cancel)
        try:
            sep = worker.call({"cmd": "separate", "input": str(orig_wav), "out_dir": str(work)})
            vocals = _read_mono(Path(sep["vocals"]), MIX_SR)
            background, _ = sf.read(sep["background"], dtype="float32", always_2d=True)
            refs = _build_speaker_refs(units, vocals, work)
            if not refs:
                raise RuntimeError("Не удалось вырезать образец голоса ни для одного участника")
            fallback_ref = next(iter(refs.values()))

            # 4. synthesis
            def _tts(batch: List[Dict[str, Any]], lo: int, hi: int, label: str) -> None:
                items = [{"id": u["id"], "text": u["translation"], "lang": target_lang,
                          "ref": str(refs.get(u["speaker"], fallback_ref)),
                          "out": str(work / "tts" / f"u{u['id']:04d}.wav")} for u in batch]

                def _on(p: Dict[str, Any]) -> None:
                    progress(lo + int((hi - lo) * p["done"] / max(1, p["total"])), f"{label}: {p['done']} из {p['total']}")

                items.sort(key=lambda it: it["ref"])  # group by speaker: conditioning is cached per reference
                res = worker.call({"cmd": "tts", "items": items, "cfg_weight": cfg_weight, "exaggeration": exaggeration}, _on)
                by_id = {r["id"]: r for r in res["results"]}
                for u in batch:
                    u["tts_path"] = by_id[u["id"]]["out"]
                stats["tts_peak_vram_gb"] = res.get("peak_vram_gb")
                stats["tts_gen_sec"] = stats.get("tts_gen_sec", 0.0) + sum(r["gen_sec"] for r in res["results"])

            check_cancel()
            progress(22, "Загрузка Chatterbox и синтез речи голосом участника...")
            _tts(units, 22, 84, "Синтез речи")

            # 5a. measure, re-translate the clips that would need too much speed-up
            def _measure() -> None:
                for i, u in enumerate(units):
                    clip, sr = sf.read(u["tts_path"], dtype="float32")
                    clip = _trim_silence(clip if clip.ndim == 1 else clip.mean(axis=1), sr)
                    u["_clip"], u["_sr"] = clip, sr
                    nxt = units[i + 1]["start"] if i + 1 < len(units) else total_dur
                    u["slot"] = max(0.3, min(nxt - 0.05, max(u["end"], u["start"] + 0.3)) - u["start"])
                    u["room"] = max(u["slot"], nxt - 0.05 - u["start"])
                    u["tempo"] = (clip.size / sr) / u["room"]

            _measure()
            too_long = [u for u in units if u["tempo"] > TEMPO_RETRANSLATE]
            stats["retranslated"] = len(too_long)
            if too_long:
                check_cancel()
                progress(85, f"Сокращение {len(too_long)} слишком длинных фраз...")
                src_name, tgt_name = DUB_LANGUAGES.get(src_lang, src_lang), DUB_LANGUAGES[target_lang]
                model = settings_now.get("ollama_model") or "qwen2.5:7b"
                for u in too_long:
                    max_chars = max(8, int(len(u["translation"]) / u["tempo"] * 1.1))
                    u["translation"] = _shorten(ollama_url, model, u["text"], u["translation"], max_chars, src_name, tgt_name)
                _unload_ollama(ollama_url, model)
                _tts(too_long, 86, 91, "Повторный синтез")
                _measure()
        finally:
            worker.close()

        # 5b. time-fit + 6. mix
        check_cancel()
        progress(92, "Подгонка длительности и сведение...")
        n = background.shape[0]
        voice = np.zeros(n, dtype=np.float32)
        gains = []
        tempos = []
        for u in units:
            tmp_src = work / "fit" / f"t{u['id']:04d}.wav"
            sf.write(str(tmp_src), u["_clip"], u["_sr"])
            tempo = min(TEMPO_MAX, u["tempo"]) if u["tempo"] > 1.0 else 1.0
            tempos.append(tempo)
            fit_path = work / "fit" / f"f{u['id']:04d}.wav"
            _stretch_clip(tmp_src, fit_path, tempo, use_rubberband)
            clip = _read_mono(fit_path, MIX_SR)
            max_len = int(u["room"] * MIX_SR)
            if clip.size > max_len:  # still too long after the speed cap: fade out at the slot end
                clip = clip[:max_len]
                fade = min(clip.size, int(0.08 * MIX_SR))
                clip[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
            a = int(u["start"] * MIX_SR)
            orig_rms = _rms(vocals[a : a + int(u["slot"] * MIX_SR)])
            g = float(np.clip(orig_rms / (_rms(clip) + 1e-9), 0.25, 4.0)) if orig_rms > 1e-4 else 1.0
            gains.append(g)
            seg = clip[: max(0, n - a)] * g
            voice[a : a + seg.size] += seg

        mix = background + voice[:, None]
        peak = float(np.abs(mix).max())
        if peak > 0.98:
            mix *= 0.98 / peak
        dub_wav = work / "dub_mix.wav"
        sf.write(str(dub_wav), mix.astype(np.float32), MIX_SR)

        # 7. mux into a clearly labelled output file
        check_cancel()
        progress(97, "Сборка итогового файла...")
        DUBS_DIR.mkdir(parents=True, exist_ok=True)
        is_video = _has_video(media)
        ext = (".mkv" if media.suffix.lower() in (".mkv", ".webm") else ".mp4") if is_video else ".m4a"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = DUBS_DIR / f"{_safe_name(meeting.get('title') or media.stem)}.AI-dub.{target_lang}.{stamp}{ext}"
        notice = (
            f"AI-GENERATED DUB ({target_lang.upper()}): translated by a local LLM and voiced with a cloned "
            "synthetic voice (Chatterbox, Perth watermark). Not the speakers' own words or voice. Made with VerbaScribe."
        )
        args = ["-i", str(media), "-i", str(dub_wav)]
        maps = (["-map", "0:v:0", "-c:v", "copy"] if is_video else ["-vn"]) + ["-map", "1:a:0"]
        if keep_original:
            maps += ["-map", "0:a:0?"]
        args += maps + [
            "-c:a", "aac", "-b:a", "192k",
            "-metadata", f"title={meeting.get('title') or media.stem} [AI dub {target_lang.upper()}]",
            "-metadata", f"comment={notice}",
            "-metadata", "encoded_by=VerbaScribe AI dub",
            *_track_tags(0, target_lang, f"AI dub ({target_lang.upper()}) - synthetic cloned voice"),
            "-disposition:a:0", "default",
        ]
        if keep_original:
            args += [*_track_tags(1, src_lang, "Original"), "-disposition:a:1", "0"]
        if ext == ".mp4":
            args += ["-movflags", "+faststart"]
        _ffmpeg(args + [str(out_path)])
        srt_path = out_path.with_suffix(".srt")
        _write_srt(units, srt_path)

        stats.update({
            "units": len(units),
            "duration_sec": total_dur,
            "elapsed_sec": round((datetime.now() - t_start).total_seconds(), 1),
            "avg_tempo": round(float(np.mean(tempos)), 3),
            "max_tempo": round(float(np.max(tempos)), 3),
            "stretch": "rubberband" if use_rubberband else "atempo",
        })
        return {
            "lang": target_lang,
            "source_lang": src_lang,
            "path": str(out_path),
            "srt_path": str(srt_path),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stats": stats,
        }
    finally:
        if settings.get("dub_keep_workdir", "false").lower() != "true":
            shutil.rmtree(work, ignore_errors=True)
