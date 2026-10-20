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
from typing import Any, Callable, Dict, Iterator, List, Optional

from app.paths import MODELS_DIR

ONNX_MODELS: Dict[str, Dict[str, Any]] = {
    "gigaam-v2": {
        "onnx_name": "gigaam-v2-rnnt",
        "name": "GigaAM v2",
        "size_mb": 900,
        "license": "MIT",
        "languages": ["ru"],
        "desc_ru": "Самая точная модель для русского языка (Сбер). Только русский. Работает на процессоре быстрее Whisper на видеокарте.",
        "desc_en": "The most accurate model for Russian (Sber). Russian only. On the CPU it is faster than Whisper on a GPU.",
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


def catalog() -> List[Dict[str, Any]]:
    out = []
    for mid, m in ONNX_MODELS.items():
        dl = _downloads.get(mid) or {}
        out.append({"id": mid, "engine": "onnx", "name": m["name"], "size_mb": m["size_mb"], "license": m["license"],
                    "languages": m["languages"], "desc_ru": m["desc_ru"], "desc_en": m["desc_en"],
                    "installed": is_installed(mid), "downloading": bool(dl.get("active")), "error": dl.get("error", "")})
    return out


def _load(model_id: str):
    """The model and the VAD, loaded once (downloading what is missing)."""
    import onnx_asr

    with _lock:
        if model_id not in _loaded:
            model_dir(model_id).mkdir(parents=True, exist_ok=True)
            _vad_dir().mkdir(parents=True, exist_ok=True)
            asr = onnx_asr.load_model(ONNX_MODELS[model_id]["onnx_name"], model_dir(model_id))
            vad = onnx_asr.load_vad(VAD_NAME, _vad_dir())
            _loaded[model_id] = asr.with_vad(vad, max_speech_duration_s=MAX_PHRASE_SEC, min_silence_duration_ms=300,
                                             speech_pad_ms=120).with_timestamps()
        return _loaded[model_id]


def release() -> None:
    with _lock:
        _loaded.clear()


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
