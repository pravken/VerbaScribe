import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import huggingface_hub

from app.database import MODELS_DIR, get_all_settings, update_settings

WHISPER_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "large-v3-turbo",
        "repo_id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "name": "Whisper Large v3 Turbo",
        "size": "1.5 GB",
        "vram": "4+ GB VRAM",
        "desc_ru": "Рекомендуется ★ Скорость в 4 раза выше Large-v3 при почти той же точности",
        "desc_en": "Recommended ★ 4x faster than Large-v3 with near-identical accuracy",
    },
    {
        "id": "large-v3",
        "repo_id": "Systran/faster-whisper-large-v3",
        "name": "Whisper Large v3",
        "size": "2.9 GB",
        "vram": "8+ GB VRAM",
        "desc_ru": "Максимальная точность для сложного звука, акцентов и сленга",
        "desc_en": "Maximum accuracy for noisy audio, accents, and technical jargon",
    },
    {
        "id": "medium",
        "repo_id": "Systran/faster-whisper-medium",
        "name": "Whisper Medium",
        "size": "1.4 GB",
        "vram": "4+ GB / CPU",
        "desc_ru": "Сбалансированная модель для средних видеокарт и ноутбуков",
        "desc_en": "Balanced model for mid-range GPUs and laptops",
    },
    {
        "id": "small",
        "repo_id": "Systran/faster-whisper-small",
        "name": "Whisper Small",
        "size": "465 MB",
        "vram": "2+ GB / CPU",
        "desc_ru": "Лёгкая модель для быстрой работы на процессоре (CPU)",
        "desc_en": "Lightweight model for fast CPU transcription",
    },
]

_setup_state: Dict[str, Any] = {
    "active": False,
    "status": "Ожидание",
    "progress": 0,
    "downloaded_mb": 0.0,
    "total_mb": 0.0,
    "error": "",
    "logs": [],
}
_setup_lock = threading.Lock()


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    logs = _setup_state.get("logs", [])
    logs.append(line)
    if len(logs) > 100:
        logs[:] = logs[-100:]
    _setup_state["logs"] = logs
    _setup_state["status"] = msg


def _whisper_model_dir(model_id: str) -> Path:
    return MODELS_DIR / "whisper" / model_id


def is_whisper_model_installed(model_id: str) -> bool:
    # Check our clean direct directory first
    direct_dir = _whisper_model_dir(model_id)
    if (direct_dir / "model.bin").exists():
        return True

    # Also check standard HuggingFace hub cache inside models/whisper
    hf_cache = MODELS_DIR / "whisper"
    if hf_cache.exists():
        slug = model_id.replace("/", "--")
        for p in hf_cache.glob(f"*{slug}*/snapshots/*/model.bin"):
            if p.exists():
                return True
    return False


def get_whisper_model_path(model_id: str) -> str:
    """
    Returns local folder path if model was downloaded via our manager,
    otherwise returns model_id so faster-whisper can resolve it.
    """
    direct_dir = _whisper_model_dir(model_id)
    if (direct_dir / "model.bin").exists():
        return str(direct_dir)
    return model_id


def is_diarizer_installed() -> bool:
    ecapa_dir = MODELS_DIR / "spkrec-ecapa-voxceleb"
    return (ecapa_dir / "hyperparams.yaml").exists() and (
        (ecapa_dir / "embedding_model.ckpt").exists()
    )


def get_models_status() -> Dict[str, Any]:
    settings = get_all_settings()
    active_whisper = settings.get("whisper_model", "large-v3-turbo")

    whisper_list = []
    any_whisper_installed = False
    for item in WHISPER_CATALOG:
        inst = is_whisper_model_installed(item["id"])
        if inst:
            any_whisper_installed = True
        whisper_list.append({**item, "installed": inst})

    diarizer_ready = is_diarizer_installed()

    return {
        "needs_first_run_setup": not any_whisper_installed,
        "any_whisper_installed": any_whisper_installed,
        "active_whisper": active_whisper,
        "diarizer_installed": diarizer_ready,
        "whisper_catalog": whisper_list,
        "setup_state": dict(_setup_state),
    }


def _download_hf_repo_with_progress(
    repo_id: str,
    target_dir: Path,
    label: str,
    base_pct: int,
    span_pct: int,
) -> None:
    """
    Downloads all required files from a HuggingFace repo directly via HTTP stream
    into target_dir with exact byte/MB progress tracking and zero symlink issues.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    api = huggingface_hub.HfApi()
    _log(f"Получение списка файлов репозитория {repo_id}...")
    info = api.repo_info(repo_id=repo_id, files_metadata=True)

    siblings = info.siblings or []
    files_to_get = []
    total_bytes = 0
    for s in siblings:
        fname = s.rfilename
        if fname.startswith(".") or fname.endswith((".md", ".txt", ".png", ".jpg")):
            continue
        fsize = getattr(s, "size", None) or 0
        files_to_get.append((fname, fsize))
        total_bytes += fsize

    total_mb = round(total_bytes / (1024 * 1024), 1) if total_bytes > 0 else 1.0
    _setup_state["total_mb"] = total_mb
    _setup_state["downloaded_mb"] = 0.0

    downloaded_bytes = 0
    last_logged_pct = -10

    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        for fname, fsize in files_to_get:
            dest_file = target_dir / fname
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            if dest_file.exists() and fsize > 0 and dest_file.stat().st_size == fsize:
                downloaded_bytes += fsize
                continue

            url = huggingface_hub.hf_hub_url(repo_id=repo_id, filename=fname)
            _log(f"Загрузка {label}: {fname}...")

            tmp_file = dest_file.with_suffix(dest_file.suffix + ".part")
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with tmp_file.open("wb") as out_f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        out_f.write(chunk)
                        downloaded_bytes += len(chunk)
                        dl_mb = round(downloaded_bytes / (1024 * 1024), 1)
                        ratio = min(1.0, downloaded_bytes / max(1, total_bytes))
                        pct = base_pct + int(span_pct * ratio)
                        _setup_state.update(
                            {
                                "downloaded_mb": dl_mb,
                                "progress": min(99, pct),
                                "status": f"{label}: {dl_mb} МБ из {total_mb} МБ ({int(ratio * 100)}%)",
                            }
                        )
                        if int(ratio * 100) - last_logged_pct >= 20:
                            last_logged_pct = int(ratio * 100)
                            _log(f"{label}: скачано {dl_mb} / {total_mb} МБ ({int(ratio * 100)}%)")

            if dest_file.exists():
                dest_file.unlink()
            tmp_file.rename(dest_file)


def start_models_setup(
    whisper_model_id: str,
    install_diarizer: bool = True,
) -> Dict[str, Any]:
    with _setup_lock:
        if _setup_state["active"]:
            return dict(_setup_state)
        _setup_state.update(
            {
                "active": True,
                "status": "Подготовка к скачиванию ИИ-моделей...",
                "progress": 1,
                "downloaded_mb": 0.0,
                "total_mb": 0.0,
                "error": "",
                "logs": [],
            }
        )
        _log(f"Запуск установки моделей (Whisper: {whisper_model_id})")

    def _worker():
        try:
            # 1. Download Whisper Model
            item = next((x for x in WHISPER_CATALOG if x["id"] == whisper_model_id), None)
            if not item:
                raise RuntimeError(f"Неизвестная модель Whisper: {whisper_model_id}")

            target_dir = _whisper_model_dir(whisper_model_id)
            _download_hf_repo_with_progress(
                repo_id=item["repo_id"],
                target_dir=target_dir,
                label=item["name"],
                base_pct=2,
                span_pct=82 if install_diarizer else 96,
            )
            update_settings({"whisper_model": whisper_model_id})
            _log(f"Модель {item['name']} успешно установлена и выбрана по умолчанию!")

            # 2. Download Speaker Diarization Model (SpeechBrain ECAPA-TDNN)
            if install_diarizer and not is_diarizer_installed():
                _log("Загрузка голосовой модели разделения спикеров (ECAPA-TDNN)...")
                ecapa_dir = MODELS_DIR / "spkrec-ecapa-voxceleb"
                _download_hf_repo_with_progress(
                    repo_id="speechbrain/spkrec-ecapa-voxceleb",
                    target_dir=ecapa_dir,
                    label="Модель спикеров ECAPA-TDNN",
                    base_pct=85,
                    span_pct=14,
                )
                _log("Модель разделения спикеров успешно установлена!")

            _log("Все выбранные модели готовы к работе!")
            _setup_state.update(
                {
                    "active": False,
                    "status": "Установка успешно завершена!",
                    "progress": 100,
                }
            )
        except Exception as e:
            _log(f"ОШИБКА: {e}")
            _setup_state.update(
                {
                    "active": False,
                    "status": f"Ошибка загрузки: {e}",
                    "error": str(e),
                }
            )

    threading.Thread(target=_worker, daemon=True).start()
    return dict(_setup_state)


def delete_whisper_model(model_id: str) -> bool:
    direct_dir = _whisper_model_dir(model_id)
    deleted = False
    if direct_dir.exists():
        shutil.rmtree(direct_dir, ignore_errors=True)
        deleted = True

    hf_cache = MODELS_DIR / "whisper"
    if hf_cache.exists():
        slug = model_id.replace("/", "--")
        for p in hf_cache.glob(f"*{slug}*"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                deleted = True
    return deleted
