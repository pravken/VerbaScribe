"""
GigaAM and Parakeet on the video card (NVIDIA, CUDA).

ONNX Runtime comes in two builds: the CPU one installed with the program (faster-whisper needs it)
and "onnxruntime-gpu". Both are the same Python package, so the GPU build is not installed over the
CPU one: it goes to a folder of its own in the Python environment (<venv>/onnxruntime-gpu), which
is put first on sys.path when the program starts, before anything imports onnxruntime. So
- the running program's files are never overwritten (Windows locks loaded DLLs),
- program updates (pip install -r requirements.txt) leave it alone,
- removing the folder goes back to the CPU build.

The CUDA and cuDNN libraries are the ones PyTorch (CUDA 12) brings; they are loaded first.
This module imports nothing heavy: app/__init__.py calls activate() on every start.
"""

import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

TARGET = Path(sys.prefix) / "onnxruntime-gpu"
_state: Dict[str, Any] = {"installing": False, "error": "", "activated": False}
_lock = threading.Lock()


def installed() -> bool:
    return (TARGET / "onnxruntime" / "capi").is_dir()


def activate() -> bool:
    """Puts the GPU build first on sys.path (only before onnxruntime is imported)."""
    if _state["activated"]:
        return True
    if os.environ.get("VERBASCRIBE_NO_ONNX_GPU") or not installed() or "onnxruntime" in sys.modules:
        return False
    sys.path.insert(0, str(TARGET))
    _state["activated"] = True
    return True


def nvidia_present() -> bool:
    try:
        from app.vram import nvml_device

        return nvml_device() is not None
    except Exception:
        return False


def cuda_available() -> bool:
    """The loaded onnxruntime can run on the GPU."""
    if not _state["activated"]:
        return False
    try:
        import onnxruntime as rt

        return "CUDAExecutionProvider" in rt.get_available_providers()
    except Exception:
        return False


_prepared = False


def prepare_cuda() -> None:
    """Loads the CUDA / cuDNN DLLs from PyTorch's wheels before ONNX Runtime looks for them."""
    global _prepared
    if _prepared:
        return
    _prepared = True
    try:
        from app.hardware import register_cuda_dlls

        register_cuda_dlls()
        import torch  # noqa: F401 - loads cudart, cublas and cudnn 9 from its lib folder

        return
    except Exception:
        pass
    try:  # no PyTorch: ONNX Runtime looks for the nvidia-* wheels itself
        import onnxruntime as rt

        if hasattr(rt, "preload_dlls"):
            rt.preload_dlls()
    except Exception:
        pass


def providers(gpu: bool) -> list:
    if gpu:
        return [("CUDAExecutionProvider", {"device_id": 0, "arena_extend_strategy": "kSameAsRequested"}),
                "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def enabled() -> bool:
    """The setting (Settings -> Recognition) and the build: GigaAM / Parakeet jobs use the GPU."""
    from app.database import get_all_settings

    return get_all_settings().get("onnx_gpu", "true").lower() == "true" and cuda_available()


def status() -> Dict[str, Any]:
    with _lock:
        st = dict(_state)
    st.update(nvidia=nvidia_present(), installed=installed(), active=cuda_available())
    st["restart_needed"] = st["installed"] and not st["activated"]
    return st


def _cpu_version() -> str:
    from importlib.metadata import version

    return version("onnxruntime")


def install_async() -> Dict[str, Any]:
    with _lock:
        if _state["installing"]:
            return status()
        _state.update(installing=True, error="")
    threading.Thread(target=_install, daemon=True, name="onnx_gpu_install").start()
    return status()


def _install() -> None:
    from app.asr_onnx import _install_commands, _run

    try:
        spec = f"onnxruntime-gpu=={_cpu_version()}"  # the same API onnx-asr runs on now
        tmp = TARGET.with_name(TARGET.name + ".partial")
        if tmp.exists():
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        errors = []
        for cmd in _install_commands([spec, "--no-deps", "--target", str(tmp)]):
            proc = _run(cmd)
            if proc.returncode == 0 and (tmp / "onnxruntime" / "capi").is_dir():
                break
            errors.append((proc.stderr or proc.stdout or "").strip()[-300:])
        else:
            raise RuntimeError("Не удалось установить onnxruntime-gpu: " + (errors[-1] if errors else "нет uv и pip"))
        if TARGET.exists():
            import shutil

            shutil.rmtree(TARGET, ignore_errors=True)
        tmp.rename(TARGET)
        activate()  # works now if nothing has imported onnxruntime yet, else after a restart
    except Exception as e:
        with _lock:
            _state["error"] = str(e)[:300]
    finally:
        with _lock:
            _state["installing"] = False


def remove() -> None:
    """Back to the CPU build (from the next start)."""
    import shutil

    shutil.rmtree(TARGET, ignore_errors=True)


def maybe_install_on_start() -> None:
    """With an NVIDIA card and the setting on, the GPU build is fetched once in the background."""
    try:
        from app.database import get_all_settings

        if get_all_settings().get("onnx_gpu", "true").lower() != "true":
            return
    except Exception:
        return
    if not installed() and nvidia_present() and sys.platform == "win32":
        install_async()


def gpu_for_job(model_id: str) -> bool:
    """Whether a transcription job runs this model on the GPU now: enough free video memory left."""
    if not enabled():
        return False
    from app.asr_onnx import ONNX_MODELS

    need = float(ONNX_MODELS.get(model_id, {}).get("vram_gb", 3.0))
    try:
        from app.vram import gpu_memory

        mem: Optional[Dict[str, float]] = gpu_memory()
        return bool(mem) and mem["free_gb"] >= need
    except Exception:
        return False
