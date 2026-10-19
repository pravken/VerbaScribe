"""
What currently occupies the GPU, for the chips next to the GPU badge: Whisper (cached in this
process), the speaker-embedding model, the model Ollama keeps loaded (/api/ps) and the dub worker
processes. Unloading frees VRAM; preloading saves the load time of the next job.
"""

import ctypes
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.database import get_all_settings


def _ollama_base() -> str:
    url = get_all_settings().get("ollama_url") or "http://127.0.0.1:11434"
    return url.replace("localhost", "127.0.0.1").rstrip("/")


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


_nvml_lock = threading.Lock()
_nvml: Dict[str, Any] = {"lib": None, "handle": None, "failed": False}


def _nvml_memory() -> Optional[Tuple[int, int]]:
    """
    (used, total) bytes of GPU 0 from the driver's NVML library, loaded once into this process.
    Faster than starting nvidia-smi on every poll, and nothing is left hanging if the driver stalls.
    """
    with _nvml_lock:
        if _nvml["failed"]:
            return None
        try:
            if _nvml["lib"] is None:
                lib = ctypes.CDLL("nvml.dll") if sys.platform == "win32" else ctypes.CDLL("libnvidia-ml.so.1")
                if lib.nvmlInit_v2() != 0:
                    raise OSError("nvmlInit failed")
                handle = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
                    raise OSError("no GPU 0")
                _nvml.update(lib=lib, handle=handle)
            mem = _NvmlMemory()
            if _nvml["lib"].nvmlDeviceGetMemoryInfo(_nvml["handle"], ctypes.byref(mem)) != 0:
                return None
            return int(mem.used), int(mem.total)
        except Exception:
            _nvml["failed"] = True  # no NVIDIA driver: do not try again on every poll
            return None


def gpu_memory() -> Optional[Dict[str, float]]:
    """
    Device-wide used/free/total in GB (all programs), or None without an NVIDIA GPU. NVML is asked
    first: on Windows (WDDM) torch.cuda.mem_get_info misses other programs' memory.
    """
    gb = 2**30
    nv = _nvml_memory()
    if nv:
        used, total = nv
        return {"total_gb": round(total / gb, 1), "free_gb": round((total - used) / gb, 1), "used_gb": round(used / gb, 1)}
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info(0)
    except Exception:
        return None
    return {"total_gb": round(total / gb, 1), "free_gb": round(free / gb, 1), "used_gb": round((total - free) / gb, 1)}


def ollama_loaded() -> List[Dict[str, Any]]:
    """Models Ollama keeps in memory. Never starts Ollama."""
    from app.ollama_client import _is_ollama_port_open

    if not _is_ollama_port_open():
        return []
    try:
        with httpx.Client(timeout=0.8) as client:
            resp = client.get(f"{_ollama_base()}/api/ps")
            resp.raise_for_status()
            models = resp.json().get("models") or []
    except Exception:
        return []
    return [
        {"name": m.get("name") or m.get("model") or "?", "size_vram_gb": round((m.get("size_vram") or 0) / 2**30, 1),
         "expires_at": m.get("expires_at")}
        for m in models
    ]


def vram_snapshot() -> Dict[str, Any]:
    from app import transcriber
    from app.diarizer import ecapa_loaded_on
    from app.dubbing import live_workers
    from app.model_manager import format_whisper_model_name
    from app.ollama_client import format_llm_model_name

    settings = get_all_settings()
    busy = transcriber.running_job_kind()
    chips: List[Dict[str, Any]] = []

    key = transcriber.whisper_cache_key()
    wanted_whisper = settings.get("whisper_model") or "large-v3-turbo"
    chips.append({
        "kind": "whisper",
        "name": format_whisper_model_name(key[0] if key else wanted_whisper),
        "loaded": bool(key),
        "device": key[1] if key else None,
        # The transcription that uses it must finish first; preloading goes through the job queue
        "can_unload": bool(key) and busy != "transcribe",
        "can_preload": not key and settings.get("device", "cuda") == "cuda" and busy is None,
    })
    ecapa = ecapa_loaded_on()
    if ecapa and ecapa.startswith("cuda"):
        chips.append({"kind": "speakers", "name": "ECAPA", "loaded": True,
                      "can_unload": busy != "transcribe", "can_preload": False})

    ollama = ollama_loaded()
    for m in ollama:
        chips.append({"kind": "ollama", "name": m["name"], "label": format_llm_model_name(m["name"]),
                      "loaded": True, "size_gb": m["size_vram_gb"],
                      "can_unload": busy not in ("dub", "translate", "summary"), "can_preload": False})
    if not ollama:
        model = settings.get("ollama_model") or ""
        chips.append({"kind": "ollama", "name": model, "label": format_llm_model_name(model) if model else "LLM",
                      "loaded": False, "can_unload": False, "can_preload": bool(model)})

    workers = [w for w in live_workers() if w["purpose"] == "dub"]
    if workers:
        chips.append({"kind": "dub", "name": "Chatterbox / Demucs", "loaded": True, "count": len(workers),
                      "can_unload": False, "can_preload": False})

    return {"gpu": gpu_memory(), "chips": chips, "busy": busy}


def unload(kind: str, name: str = "") -> Dict[str, Any]:
    from app import transcriber

    busy = transcriber.running_job_kind()
    if kind == "whisper":
        if busy == "transcribe":
            raise RuntimeError("Whisper сейчас распознаёт запись")
        transcriber.release_whisper_model()
    elif kind == "speakers":
        if busy == "transcribe":
            raise RuntimeError("Модель распознавания участников сейчас используется")
        from app.diarizer import unload_diarizer

        unload_diarizer()
    elif kind == "ollama":
        if not name:
            raise ValueError("Не указана модель Ollama")
        with httpx.Client(timeout=30.0) as client:
            client.post(f"{_ollama_base()}/api/generate", json={"model": name, "keep_alive": 0}).raise_for_status()
    elif kind == "dub":
        raise RuntimeError("Процессы дубляжа завершаются вместе с задачей — отмените её в очереди")
    else:
        raise ValueError(f"Неизвестный тип модели {kind!r}")
    return vram_snapshot()


def preload(kind: str) -> Dict[str, Any]:
    from app import transcriber

    if kind == "whisper":
        if not transcriber.enqueue_whisper_preload():
            raise RuntimeError("Очередь занята — модель загрузится вместе со следующей задачей")
    elif kind == "ollama":
        from app.ollama_client import ensure_ollama_running

        model = get_all_settings().get("ollama_model") or ""
        if not model:
            raise ValueError("Модель Ollama не выбрана в настройках")
        base = _ollama_base()
        ensure_ollama_running(base)  # explicit user action: starting Ollama is expected here
        with httpx.Client(timeout=120.0) as client:
            # An empty prompt only loads the model; it stays for Ollama's usual keep-alive time
            client.post(f"{base}/api/generate", json={"model": model, "keep_alive": "10m"}).raise_for_status()
    else:
        raise ValueError(f"Модель {kind!r} нельзя загрузить заранее")
    return vram_snapshot()
