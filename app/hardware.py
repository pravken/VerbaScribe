import os
import sys
import gc
from pathlib import Path
from typing import Dict, Any

# Suppress symlink warnings and force UTF-8 output on Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["PYTHONUTF8"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def register_cuda_dlls() -> None:
    """
    Automatically locates and registers NVIDIA cuBLAS and cuDNN DLLs installed inside
    the Python virtual environment so CTranslate2 (faster-whisper) and PyTorch (SpeechBrain)
    coexist on Windows without DLL symbol conflicts (such as cudnnGetLibConfig Error 127).
    """
    if sys.platform != "win32":
        return

    import ctypes

    site_packages_candidates = [
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia",
        Path(__file__).resolve().parent.parent / ".venv" / "Lib" / "site-packages" / "nvidia",
    ]

    dll_dirs = []
    # Prioritize torch/lib first so its newer cuDNN 9 DLLs take precedence over older shims
    for base in site_packages_candidates:
        torch_lib = base.parent / "torch" / "lib"
        if torch_lib.exists():
            dll_dirs.append(str(torch_lib.resolve()))
            for cudnn_dll in (
                "cudnn64_9.dll",
                "cudnn_graph64_9.dll",
                "cudnn_ops64_9.dll",
                "cudnn_cnn64_9.dll",
            ):
                dll_file = torch_lib / cudnn_dll
                if dll_file.exists():
                    try:
                        ctypes.WinDLL(str(dll_file))
                    except Exception:
                        pass

    for base in site_packages_candidates:
        if base.exists():
            for bin_dir in base.glob("*/bin"):
                if bin_dir.is_dir():
                    dll_dirs.append(str(bin_dir.resolve()))

    current_path = os.environ.get("PATH", "")
    for d in reversed(dll_dirs):
        if d not in current_path:
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass


# Register DLL directories immediately on import
register_cuda_dlls()


def get_hardware_info() -> Dict[str, Any]:
    """
    Detects available GPU/CPU resources and recommends optimal Whisper model and compute_type
    for any user's hardware (NVIDIA high-VRAM, NVIDIA low-VRAM, or AMD/Intel/CPU fallback).
    """
    info: Dict[str, Any] = {
        "cuda_available": False,
        "device_name": "Процессор (CPU)",
        "vram_total_gb": 0.0,
        "vram_free_gb": 0.0,
        "recommended_device": "cpu",
        "recommended_compute_type": "int8",
        "recommended_whisper_model": "small",
        "recommended_ollama_model": "qwen3:4b",
        "tier": "cpu",
        "tier_description": "Работа на процессоре (рекомендуются модели small / medium / large-v3-turbo в режиме int8)",
    }

    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            total_gb = round(props.total_memory / (1024**3), 1)
            try:
                free_mem, _ = torch.cuda.mem_get_info(0)
                free_gb = round(free_mem / (1024**3), 1)
            except Exception:
                free_gb = total_gb

            info["cuda_available"] = True
            info["device_name"] = props.name
            info["vram_total_gb"] = total_gb
            info["vram_free_gb"] = free_gb
            info["recommended_device"] = "cuda"

            if total_gb >= 10.0:
                info["tier"] = "high_vram"
                info["recommended_compute_type"] = "float16"
                info["recommended_whisper_model"] = "large-v3-turbo"
                info["recommended_ollama_model"] = "qwen3:8b"
                info["tier_description"] = (
                    f"Максимальная производительность ({props.name}, {total_gb} ГБ VRAM). "
                    "Доступны самые точные модели large-v3 и large-v3-turbo в режиме float16."
                )
            elif total_gb >= 5.5:
                info["tier"] = "mid_vram"
                info["recommended_compute_type"] = "int8_float16"
                info["recommended_whisper_model"] = "large-v3-turbo"
                info["recommended_ollama_model"] = "qwen3:8b"
                info["tier_description"] = (
                    f"Средний объём видеопамяти ({props.name}, {total_gb} ГБ VRAM). "
                    "Рекомендуется модель large-v3-turbo в гибридном режиме int8_float16."
                )
            else:
                info["tier"] = "low_vram"
                info["recommended_compute_type"] = "int8"
                info["recommended_whisper_model"] = "medium"
                info["recommended_ollama_model"] = "qwen3:4b"
                info["tier_description"] = (
                    f"Компактный режим видеопамяти ({props.name}, {total_gb} ГБ VRAM). "
                    "Модели загружаются по очереди в сжатом формате int8."
                )
    except Exception as e:
        info["error"] = str(e)

    return info


def clear_vram() -> None:
    """
    Explicitly releases GPU memory between pipeline stages (e.g. after Whisper and before
    Diarization or Ollama LLM summary) so even 4-6 GB GPUs never run out of memory.
    """
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass
