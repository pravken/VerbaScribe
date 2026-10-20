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
        "languages": ["ru"],
        "desc_ru": "Новая модель Сбера для русского языка: сразу пишет со знаками препинания и заглавными буквами, числа цифрами. Только русский, работает на процессоре.",
        "desc_en": "Sber's new model for Russian: writes with punctuation and capital letters, numbers as digits. Russian only, runs on the CPU.",
    },
    "gigaam-v2": {
        "onnx_name": "gigaam-v2-rnnt",
        "name": "GigaAM v2",
        "size_mb": 900,
        "license": "MIT",
        "languages": ["ru"],
        "desc_ru": "Модель Сбера для русского языка, без знаков препинания и заглавных букв (их пишет GigaAM v3). Только русский, работает на процессоре.",
        "desc_en": "Sber's model for Russian, without punctuation and capital letters (GigaAM v3 writes them). Russian only, runs on the CPU.",
    },
    "parakeet-v3": {
        "onnx_name": "nemo-parakeet-tdt-0.6b-v3",
        "name": "Parakeet TDT v3",
        "size_mb": 2600,
        "license": "CC-BY-4.0 (NVIDIA)",
        "languages": ["en", "ru", "de", "fr", "es", "it", "pt", "nl", "pl", "uk", "cs", "sk", "bg", "hr", "da",
                      "fi", "el", "hu", "lv", "lt", "mt", "ro", "sl", "sv", "et"],
        "desc_ru": "Очень быстрая модель NVIDIA для 25 европейских языков, включая русский и английский, с расстановкой знаков препинания.",
        "desc_en": "A very fast NVIDIA model for 25 European languages including English and Russian, with punctuation.",
    },
}
VAD_NAME = "silero"
MAX_PHRASE_SEC = 20.0

_lock = threading.Lock()
_loaded: Dict[str, Any] = {}
_downloads: Dict[str, Dict[str, Any]] = {}


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


def _install_commands():
    """uv first (setup.bat makes the venv with uv, which has no pip in it), then pip, bootstrapping it
    with ensurepip when the venv has none."""
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
        yield [uv, "pip", "install", "--python", sys.executable, ONNX_ASR_PIN]
    if _run([sys.executable, "-m", "pip", "--version"]).returncode != 0:
        _run([sys.executable, "-m", "ensurepip", "--upgrade"])
    yield [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", ONNX_ASR_PIN]


def _load(model_id: str):
    """The model and the VAD, loaded once (installing the library and downloading what is missing)."""
    ensure_package()
    import onnx_asr

    with _lock:
        if model_id not in _loaded:
            fetch(model_id)
            asr = onnx_asr.load_model(ONNX_MODELS[model_id]["onnx_name"], model_dir(model_id))
            vad = onnx_asr.load_vad(VAD_NAME, _vad_dir())
            _loaded[model_id] = asr.with_vad(vad, max_speech_duration_s=MAX_PHRASE_SEC, min_silence_duration_ms=300,
                                             speech_pad_ms=120).with_timestamps()
        return _loaded[model_id]


def fetch(model_id: str) -> None:
    """Downloads the missing model files into our models folder. onnx_asr.load_model with a path that
    already exists only looks there and never downloads, so the files are fetched with its resolver
    (offline=False: look in the folder, download from Hugging Face what is not there)."""
    from onnx_asr.loader import create_asr_resolver, create_vad_resolver

    create_asr_resolver(ONNX_MODELS[model_id]["onnx_name"], model_dir(model_id), offline=False).resolve_model()
    create_vad_resolver(VAD_NAME, _vad_dir(), offline=False).resolve_model()


def release(model_id: Optional[str] = None) -> None:
    with _lock:
        if model_id:
            _loaded.pop(model_id, None)
        else:
            _loaded.clear()


def preload(model_id: str) -> None:
    try:
        _load(model_id)
    except Exception:
        traceback.print_exc()


def loaded_models() -> List[str]:
    return list(_loaded)


def guess_language(text: str) -> str:
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return "ru" if cyr > len(text) * 0.3 else "en"


def recognize_array(model_id: str, audio: Any) -> List[Tuple[float, float, str]]:
    """Phrases of a short 16 kHz float32 window (live captions): [(start, end, text)]."""
    asr = _load(model_id)
    out = []
    for res in asr.recognize(audio, sample_rate=16000):
        text = " ".join((res.text or "").split())
        if text:
            out.append((float(res.start), float(res.end), text))
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


def words_from_tokens(tokens: List[str], timestamps: List[float], offset: float, end: float) -> List[Dict[str, Any]]:
    """Joins sub-word tokens into words: a token starting with a space (or "▁") starts a new word.
    A word lasts until the next word starts (the last one until the end of the phrase)."""
    words: List[Dict[str, Any]] = []
    for tok, ts in zip(tokens, timestamps):
        piece = tok.replace("▁", " ")
        if not piece:
            continue
        if piece[0].isspace() or not words:
            if piece.strip():
                words.append({"word": " " + piece.strip(), "start": round(offset + float(ts), 2)})
        else:
            words[-1]["word"] += piece
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else end
        w["end"] = round(max(w["start"] + 0.05, min(nxt, w["start"] + 2.0)), 2)
        w["prob"] = 1.0
    return words


def transcribe(model_id: str, wav_path: Path, total_dur: float, on_progress: Callable[[float], None],
               check_cancel: Callable[[], None], out: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Segments like faster-whisper's: [{start, end, text, words: [{word, start, end, prob}]}].
    `out` (optional) receives them as they come, for showing the text while it is recognized."""
    asr = _load(model_id)
    results: Iterator[Any] = asr.recognize(str(wav_path))
    segments: List[Dict[str, Any]] = out if out is not None else []
    for res in results:
        check_cancel()
        text = " ".join((res.text or "").split())
        if not text:
            continue
        start, end = float(res.start), float(res.end)
        words = words_from_tokens(res.tokens or [], res.timestamps or [], start, end) if res.tokens and res.timestamps else []
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text, "words": words})
        on_progress(min(1.0, end / max(1.0, total_dur)))
    return segments
