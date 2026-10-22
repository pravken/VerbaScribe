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
NEW = TARGET.with_name(TARGET.name + ".new")           # a build waiting to replace TARGET at the next start
REMOVE_MARK = TARGET.with_name(TARGET.name + ".remove")  # TARGET is deleted at the next start
_state: Dict[str, Any] = {"installing": False, "error": "", "activated": False}
_lock = threading.Lock()


def _complete(d: Path) -> bool:
    return (d / "onnxruntime" / "__init__.py").is_file() and (d / "onnxruntime" / "capi").is_dir()


def installed() -> bool:
    return not REMOVE_MARK.exists() and (_complete(TARGET) or _complete(NEW))


def _rmtree(d: Path) -> bool:
    import shutil

    shutil.rmtree(d, ignore_errors=True)
    return not d.exists()


def _finish_pending() -> None:
    """What a running program could not do: its DLLs were loaded (Windows keeps them locked)."""
    if REMOVE_MARK.exists():
        if _rmtree(TARGET) and _rmtree(NEW):
            REMOVE_MARK.unlink(missing_ok=True)
    elif _complete(NEW) and _rmtree(TARGET):
        NEW.rename(TARGET)


def activate() -> bool:
    """Puts the GPU build first on sys.path (only before onnxruntime is imported)."""
    if _state["activated"]:
        return True
    if "onnxruntime" not in sys.modules:
        try:
            _finish_pending()
        except OSError:
            pass
    if os.environ.get("VERBASCRIBE_NO_ONNX_GPU") or not _complete(TARGET) or REMOVE_MARK.exists() \
            or "onnxruntime" in sys.modules:
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
    st.update(nvidia=nvidia_present(), installed=installed(), active=cuda_available(),
              removal_pending=REMOVE_MARK.exists() and TARGET.exists())
    st["restart_needed"] = st["installed"] and (not st["activated"] or _complete(NEW))
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


# The GPU wheel is checked like every package of requirements.txt: by its hash on PyPI (the Python
# 3.10 build setup.bat makes the environment with). A version without a known hash is not installed.
WHEEL_HASHES = {
    ("1.23.2", "cp310"): "deba091e15357355aa836fd64c6c4ac97dd0c4609c38b08a69675073ea46b321",
}


def _requirement() -> str:
    """The requirements line with the hash, or a RuntimeError when this combination is unknown."""
    version = _cpu_version()  # the same API onnx-asr runs on now
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    digest = WHEEL_HASHES.get((version, tag))
    if not digest:
        raise RuntimeError(f"Нет проверенной контрольной суммы onnxruntime-gpu {version} для Python {tag[2:3]}.{tag[3:]}: "
                           "обновите программу")
    return f"onnxruntime-gpu=={version} --hash=sha256:{digest}"


def _install() -> None:
    from app.asr_onnx import _install_commands, _run

    try:
        REMOVE_MARK.unlink(missing_ok=True)
        tmp = TARGET.with_name(TARGET.name + ".partial")
        _rmtree(tmp)
        req = TARGET.with_name("onnxruntime-gpu-requirements.txt")
        req.write_text(_requirement() + "\n", encoding="utf-8")
        errors = []
        for cmd in _install_commands(["-r", str(req), "--require-hashes", "--no-deps", "--target", str(tmp)]):
            proc = _run(cmd)
            if proc.returncode == 0 and (tmp / "onnxruntime" / "capi").is_dir():
                break
            errors.append((proc.stderr or proc.stdout or "").strip()[-300:])
        else:
            raise RuntimeError("Не удалось установить onnxruntime-gpu: " + (errors[-1] if errors else "нет uv и pip"))
        _rmtree(NEW)
        tmp.rename(NEW)
        if not _state["activated"] and "onnxruntime" not in sys.modules:
            _finish_pending()
            activate()  # works now: nothing has imported onnxruntime yet
        # else NEW replaces TARGET at the next start
    except Exception as e:
        with _lock:
            _state["error"] = str(e)[:300]
    finally:
        with _lock:
            _state["installing"] = False


def remove() -> None:
    """Back to the CPU build. A loaded build cannot be deleted (its DLLs are locked): the rest goes at
    the next start."""
    _rmtree(NEW)
    if TARGET.exists() and not _rmtree(TARGET):
        REMOVE_MARK.touch()


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
