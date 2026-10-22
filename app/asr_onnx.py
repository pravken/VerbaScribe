"""
Speech recognition with GigaAM v2 (Sber, MIT licence) and NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0),
both free for commercial use, run on ONNX Runtime through onnx-asr (MIT).

GigaAM is the most accurate model for Russian; Parakeet is several times faster than Whisper and
covers 25 European languages. Neither needs PyTorch. The audio is cut into phrases by the Silero
VAD (MIT), each phrase is recognized with token timestamps, and the tokens are joined into words,
so the result has the same shape as the Whisper segments (start, end, text, words) and the rest of
the pipeline (speakers, translation, subtitles) does not change.

Models are downloaded from Hugging Face (the ONNX exports in istupakov/*) into <models>/onnx.
"""

import re
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from app.paths import MODELS_DIR

ONNX_MODELS: Dict[str, Dict[str, Any]] = {
    "gigaam-v3": {
        "onnx_name": "gigaam-v3-e2e-rnnt",
        "name": "GigaAM v3",
        "size_mb": 900,
        "license": "MIT",
        "vram_gb": 1.5,  # on the GPU (app/onnx_gpu.py)
        "languages": ["ru"],
        "desc_ru": "Новая модель Сбера для русского языка: сразу пишет со знаками препинания и заглавными буквами, числа цифрами. Только русский, работает на процессоре.",
        "desc_en": "Sber's new model for Russian: writes with punctuation and capital letters, numbers as digits. Russian only, runs on the CPU.",
    },
    "parakeet-v3": {
        "onnx_name": "nemo-parakeet-tdt-0.6b-v3",
        "name": "Parakeet TDT v3",
        "size_mb": 2600,
        "license": "CC-BY-4.0 (NVIDIA)",
        "vram_gb": 3.0,
        "languages": ["en", "ru", "de", "fr", "es", "it", "pt", "nl", "pl", "uk", "cs", "sk", "bg", "hr", "da",
                      "fi", "el", "hu", "lv", "lt", "mt", "ro", "sl", "sv", "et"],
        "desc_ru": "Очень быстрая модель NVIDIA для 25 европейских языков, включая русский и английский, с расстановкой знаков препинания.",
        "desc_en": "A very fast NVIDIA model for 25 European languages including English and Russian, with punctuation.",
    },
}
VAD_NAME = "silero"
MAX_PHRASE_SEC = 20.0

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_loaded: Dict[str, Any] = {}
_downloads: Dict[str, Dict[str, Any]] = {}


# Removed models: what a setting or a recording that still names one now uses
REPLACED = {"gigaam-v2": "gigaam-v3", "canary-v2": "parakeet-v3"}  # Canary: slower and less accurate in Russian (bench-report/)


def is_onnx_model(model_id: str) -> bool:
    return model_id in ONNX_MODELS


def model_dir(model_id: str) -> Path:
    return MODELS_DIR / "onnx" / model_id


def _vad_dir() -> Path:
    return MODELS_DIR / "onnx" / "silero-vad"


def is_installed(model_id: str) -> bool:
    d = model_dir(model_id)
    return d.is_dir() and any(d.glob("*.onnx")) and any(_vad_dir().glob("*.onnx"))


def supports_language(model_id: str, lang: Optional[str]) -> bool:
    return not lang or lang == "auto" or lang[:2] in ONNX_MODELS[model_id]["languages"]


def _dir_mb(d: Path) -> float:
    """Size of a folder, including the partial files Hugging Face writes while downloading."""
    try:
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)
    except OSError:
        return 0.0


def download_progress(model_id: str) -> int:
    return max(1, min(99, int(_dir_mb(model_dir(model_id)) * 100 / ONNX_MODELS[model_id]["size_mb"])))


def catalog() -> List[Dict[str, Any]]:
    out = []
    for mid, m in ONNX_MODELS.items():
        dl = _downloads.get(mid) or {}
        out.append({"id": mid, "engine": "onnx", "name": m["name"], "size_mb": m["size_mb"], "license": m["license"],
                    "languages": m["languages"], "desc_ru": m["desc_ru"], "desc_en": m["desc_en"],
                    "installed": is_installed(mid), "downloading": bool(dl.get("active")),
                    "progress": download_progress(mid) if dl.get("active") else 0, "error": dl.get("error", "")})
    return out


ONNX_ASR_PIN = "onnx-asr==0.12.0"


def ensure_package() -> None:
    """Installs onnx-asr into the program's Python when it is missing (an update copied over an old
    installation brings the code but not the new library)."""
    import importlib

    try:
        importlib.import_module("onnx_asr")
        return
    except ImportError:
        pass
    print(f"[onnx] installing {ONNX_ASR_PIN}")
    errors = []
    for cmd in _install_commands():
        proc = _run(cmd)
        if proc.returncode == 0:
            importlib.invalidate_caches()
            return
        errors.append((proc.stderr or proc.stdout or "").strip()[-300:])
    raise RuntimeError("Не удалось установить onnx-asr: " + (errors[-1] if errors else "нет uv и pip"))


def _run(cmd):
    import subprocess

    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def _install_commands(args=None):
    """uv first (setup.bat makes the venv with uv, which has no pip in it), then pip, bootstrapping it
    with ensurepip when the venv has none. `args`: what to install (onnx-asr by default)."""
    import os
    import shutil
    import sys

    uv = shutil.which("uv")
    if not uv:
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        for c in (home / ".local" / "bin" / "uv.exe", home / ".cargo" / "bin" / "uv.exe",
                  Path(os.environ.get("LOCALAPPDATA") or home) / "Microsoft" / "WinGet" / "Links" / "uv.exe"):
            if c.is_file():
                uv = str(c)
                break
    if uv:
        yield [uv, "pip", "install", "--python", sys.executable, *(args or [ONNX_ASR_PIN])]
    if _run([sys.executable, "-m", "pip", "--version"]).returncode != 0:
        _run([sys.executable, "-m", "ensurepip", "--upgrade"])
    yield [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *(args or [ONNX_ASR_PIN])]


def _key(model_id: str, gpu: bool) -> str:
    return f"{model_id}@gpu" if gpu else model_id


def _load(model_id: str, gpu: bool = False):
    """The model and the VAD, loaded once (installing the library and downloading what is missing).
    gpu: on the video card (app/onnx_gpu.py; a copy apart from the CPU one live captions use). The VAD
    stays on the CPU: it is tiny, and the GPU would only add copies."""
    ensure_package()
    import onnx_asr

    from app import onnx_gpu

    if model_id not in _loaded and _key(model_id, True) not in _loaded:
        with _fetch_lock:  # the download (gigabytes) must not hold the lock live captions wait on
            fetch(model_id)
    key = _key(model_id, gpu)
    with _lock:
        if key not in _loaded:
            if gpu:
                onnx_gpu.prepare_cuda()
            asr = onnx_asr.load_model(ONNX_MODELS[model_id]["onnx_name"], model_dir(model_id),
                                      providers=onnx_gpu.providers(gpu))
            vad = onnx_asr.load_vad(VAD_NAME, _vad_dir(), providers=onnx_gpu.providers(False))
            _loaded[key] = asr.with_vad(vad, max_speech_duration_s=MAX_PHRASE_SEC, min_silence_duration_ms=300,
                                        speech_pad_ms=120).with_timestamps()
        return _loaded[key]


def fetch(model_id: str) -> None:
    """Downloads the missing model files into our models folder. onnx_asr.load_model with a path that
    already exists only looks there and never downloads, so the files are fetched with its resolver
    (offline=False: look in the folder, download from Hugging Face what is not there)."""
    from onnx_asr.loader import create_asr_resolver, create_vad_resolver

    create_asr_resolver(ONNX_MODELS[model_id]["onnx_name"], model_dir(model_id), offline=False).resolve_model()
    create_vad_resolver(VAD_NAME, _vad_dir(), offline=False).resolve_model()


def release(model_id: Optional[str] = None, gpu: Optional[bool] = None) -> None:
    """gpu None: both copies of the model."""
    with _lock:
        if model_id:
            for g in ((False, True) if gpu is None else (gpu,)):
                _loaded.pop(_key(model_id, g), None)
        else:
            _loaded.clear()


def on_gpu(model_id: str) -> bool:
    return _key(model_id, True) in _loaded


def release_gpu() -> None:
    """Frees the video memory of the models a transcription job loaded there."""
    with _lock:
        for k in [k for k in _loaded if k.endswith("@gpu")]:
            _loaded.pop(k, None)


def preload(model_id: str) -> None:
    try:
        _load(model_id)
    except Exception:
        traceback.print_exc()


def loaded_models() -> List[str]:
    return list(dict.fromkeys(k.split("@")[0] for k in _loaded))


def guess_language(text: str) -> str:
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return "ru" if cyr > len(text) * 0.3 else "en"


_UNK_IN_WORD = re.compile(r"(?<=[а-яё])<unk>(?=[а-яё])", re.IGNORECASE)


def clean_text(text: str) -> str:
    """GigaAM writes "ё", which its vocabulary lacks, as "<unk>" ("ид<unk>т"): "е" inside a Russian
    word, nothing elsewhere."""
    if "<unk>" not in text:
        return text
    return " ".join(_UNK_IN_WORD.sub("е", text).replace("<unk>", "").split())


def _clean_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for w in words:
        word = clean_text(w["word"])
        if word.strip():
            out.append({**w, "word": " " + word.strip()})
    return out


QUIET_PEAK = 0.3     # below this the sound is raised, so the VAD does not take quiet speech for silence
SILENT_PEAK = 0.002  # nothing but noise: left as it is
MAX_GAIN = 30.0


def level(audio: Any) -> Any:
    """A quiet recording (far from the microphone, low gain) brought to a normal level: the VAD of
    GigaAM / Parakeet drops whole quiet phrases otherwise (bench-report/gpu-report.md)."""
    import numpy as np

    a = np.asarray(audio, dtype=np.float32)
    if a.size == 0:
        return a
    peak = float(np.percentile(np.abs(a), 99.9))  # not one click
    if SILENT_PEAK < peak < QUIET_PEAK:
        a = np.clip(a * min(0.9 / peak, MAX_GAIN), -1.0, 1.0)
    return a


def recognize_array(model_id: str, audio: Any) -> List[Tuple[float, float, str]]:
    """Sentences of a short 16 kHz float32 window (live captions): [(start, end, text)], cut at the
    word timings, so a sentence ends exactly where the next one starts."""
    asr = _load(model_id)
    out = []
    for res in asr.recognize(level(audio), sample_rate=16000):
        text = clean_text(" ".join((res.text or "").split()))
        if not text:
            continue
        start, end = float(res.start), float(res.end)
        if res.tokens and res.timestamps:
            seg = {"start": start, "end": end, "text": text,
                   "words": _clean_words(words_from_text(" ".join((res.text or "").split()), res.tokens, res.timestamps,
                                                         start, end))}
            out.extend((p["start"], p["end"], p["text"]) for p in split_sentences_words(seg))
        else:
            out.extend(split_sentences_text(start, end, text))
    return out


def start_download(model_id: str) -> Dict[str, Any]:
    """Downloads the model in the background (loading it once fetches every file it needs)."""
    if model_id not in ONNX_MODELS:
        raise ValueError("Неизвестная модель")
    if (_downloads.get(model_id) or {}).get("active"):
        return _downloads[model_id]
    _downloads[model_id] = {"active": True, "error": ""}

    def work() -> None:
        try:
            _load(model_id)
            _downloads[model_id] = {"active": False, "error": ""}
        except Exception as e:
            traceback.print_exc()
            _downloads[model_id] = {"active": False, "error": str(e)[:300]}

    threading.Thread(target=work, daemon=True, name=f"onnx_dl_{model_id}").start()
    return _downloads[model_id]


def delete_model(model_id: str) -> bool:
    import shutil

    release()
    d = model_dir(model_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def words_from_text(text: str, tokens: List[str], timestamps: List[float], offset: float, end: float) -> List[Dict[str, Any]]:
    """Words exactly as in the model's text (its spaces), each starting at the time of the token that
    holds its first letter: tokens are matched to the text by their non-space characters."""
    starts: List[float] = []  # time of every non-space character of the token stream
    for tok, ts in zip(tokens, timestamps):
        starts.extend([float(ts)] * sum(1 for ch in tok.replace("▁", " ") if not ch.isspace()))
    words: List[Dict[str, Any]] = []
    last: List[float] = []  # time of the last token of each word
    pos = 0
    for w in text.split():
        if pos >= len(starts):
            return words_from_tokens(tokens, timestamps, offset, end)  # text and tokens differ
        words.append({"word": " " + w, "start": round(offset + starts[pos], 2)})
        last.append(offset + starts[min(len(starts), pos + len(w)) - 1])
        pos += len(w)
    if pos != len(starts):
        return words_from_tokens(tokens, timestamps, offset, end)
    _set_ends(words, last, end)
    return words


WORD_TAIL_SEC = 0.25  # a word goes on this long after its last token is emitted


def _set_ends(words: List[Dict[str, Any]], last: List[float], end: float) -> None:
    """A word ends a little after its last token, not where the next one starts: the pauses between
    words stay visible, and speaker separation cuts long phrases there (app/diarizer.py)."""
    for i, w in enumerate(words):
        if i + 1 < len(words):
            stop = min(words[i + 1]["start"], last[i] + WORD_TAIL_SEC)
        else:
            stop = end  # the phrase ends where the VAD heard the speech end
        w["end"] = round(max(w["start"] + 0.05, min(stop, w["start"] + 2.0)), 2)
        w["prob"] = 1.0


def words_from_tokens(tokens: List[str], timestamps: List[float], offset: float, end: float) -> List[Dict[str, Any]]:
    """Joins sub-word tokens into words: a token starting with a space (or "▁") starts a new word.
    A word lasts until the next word starts (the last one until the end of the phrase)."""
    words: List[Dict[str, Any]] = []
    last: List[float] = []
    space = True  # a space before the next piece (GigaAM v3 gives it as a separate "▁" token)
    for tok, ts in zip(tokens, timestamps):
        piece = tok.replace("▁", " ")
        if not piece:
            continue
        if not piece.strip():
            space = True
            continue
        if space or piece[0].isspace() or not words:
            words.append({"word": " " + piece.strip(), "start": round(offset + float(ts), 2)})
            last.append(offset + float(ts))
        else:
            words[-1]["word"] += piece.rstrip()
            last[-1] = offset + float(ts)
        space = piece[-1].isspace()
    _set_ends(words, last, end)
    return words


_SENT_END = (".", "?", "!", "…")


def split_sentences_words(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A phrase of several sentences as one segment per sentence (cut at words that end one)."""
    words = seg.get("words") or []
    if len(words) < 2:
        return [seg]
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        if w["word"].strip().endswith(_SENT_END) and i < len(words) - 1:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    if len(out) == 1:
        return [seg]
    return [{"start": ws[0]["start"], "end": ws[-1]["end"], "text": "".join(w["word"] for w in ws).strip(), "words": ws}
            for ws in out]


def split_sentences_text(start: float, end: float, text: str) -> List[Tuple[float, float, str]]:
    """The same without word timings: times shared by the length of each sentence."""
    import re

    parts = [p.strip() for p in re.split(r"(?<=[.?!…])\s+", text) if p.strip()]
    if len(parts) < 2:
        return [(start, end, text)]
    total = sum(len(p) for p in parts)
    out, t = [], start
    for p in parts:
        d = (end - start) * len(p) / total
        out.append((round(t, 2), round(t + d, 2), p))
        t += d
    return out


def transcribe(model_id: str, wav_path: Path, total_dur: float, on_progress: Callable[[float], None],
               check_cancel: Callable[[], None], out: Optional[List[Dict[str, Any]]] = None,
               gpu: bool = False) -> List[Dict[str, Any]]:
    """Segments like faster-whisper's: [{start, end, text, words: [{word, start, end, prob}]}].
    `out` (optional) receives them as they come, for showing the text while it is recognized."""
    import soundfile as sf

    asr = _load(model_id, gpu)
    audio, sr = sf.read(str(wav_path), dtype="float32")  # 16 kHz mono (app/audio_utils.py)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    results: Iterator[Any] = asr.recognize(level(audio), sample_rate=sr)
    segments: List[Dict[str, Any]] = out if out is not None else []
    for res in results:
        check_cancel()
        raw = " ".join((res.text or "").split())
        text = clean_text(raw)
        if not text:
            continue
        start, end = float(res.start), float(res.end)
        words = _clean_words(words_from_text(raw, res.tokens, res.timestamps, start, end)) if res.tokens and res.timestamps else []
        seg = {"start": round(start, 2), "end": round(end, 2), "text": text, "words": words}
        segments.extend(split_sentences_words(seg) if words else
                        [{"start": a, "end": b, "text": t, "words": []} for a, b, t in split_sentences_text(start, end, text)])
        on_progress(min(1.0, end / max(1.0, total_dur)))
    return segments
