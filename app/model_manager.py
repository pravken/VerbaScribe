import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import huggingface_hub

from app.database import MODELS_DIR, get_all_settings, update_settings

WHISPER_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "large-v3-turbo",
        # Moved to dropbox-dash/faster-whisper-large-v3-turbo; the old name redirects there. Kept for
        # the folder name of earlier downloads (models--mobiuslabsgmbh--...)
        "repo_id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "name": "Whisper Turbo",
        "badge": "1.5 GB · MIT",
        "size": "1.5 GB",
        "vram": "4+ GB VRAM",
        "desc_ru": "Рекомендуется — скорость в 4 раза выше Pro-версии при почти той же точности",
        "desc_en": "Recommended — 4x faster than Pro with near-identical accuracy",
    },
    {
        "id": "large-v3",
        "repo_id": "Systran/faster-whisper-large-v3",
        "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "name": "Whisper Pro",
        "badge": "2.9 GB · MIT",
        "size": "2.9 GB",
        "vram": "8+ GB VRAM",
        "desc_ru": "Максимальная точность для сложного звука, акцентов и сленга",
        "desc_en": "Maximum accuracy for noisy audio, accents, and technical jargon",
    },
]

# Removed Whisper models (Turbo is as large as Medium and more accurate; GigaAM / Parakeet replace
# Light on the CPU): what a setting or a recording that still names one now uses
WHISPER_REPLACED = {"medium": "large-v3-turbo", "small": "large-v3-turbo"}


def remove_retired_models() -> None:
    """Moves settings off removed models (Whisper and ONNX) and deletes their files."""
    from app import asr_onnx

    replaced = {**WHISPER_REPLACED, **asr_onnx.REPLACED}
    settings = get_all_settings()
    changes = {k: replaced[v] for k in ("whisper_model", "live_whisper_model") if (v := settings.get(k)) in replaced}
    if changes:
        update_settings(changes)
    for old in WHISPER_REPLACED:
        delete_whisper_model(old)
    for old in asr_onnx.REPLACED:
        if asr_onnx.model_dir(old).is_dir():
            shutil.rmtree(asr_onnx.model_dir(old), ignore_errors=True)


_WHISPER_DISPLAY_NAMES: Dict[str, str] = {
    "large-v3-turbo": "Whisper Turbo",
    "large-v3": "Whisper Pro",
    "medium": "Whisper Medium",
    "small": "Whisper Light",
}


def format_whisper_model_name(model_id: str) -> str:
    raw = (model_id or "").strip()
    if not raw:
        return "Whisper"
    from app.asr_onnx import ONNX_MODELS

    if raw in ONNX_MODELS:
        return ONNX_MODELS[raw]["name"]
    return _WHISPER_DISPLAY_NAMES.get(raw.lower(), f"Whisper {raw.replace('-', ' ').title()}")


_setup_state: Dict[str, Any] = {
    "active": False,
    "model_id": "",
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


# Every Whisper model id the app knows, with its repository (removed ones too, to delete them)
_WHISPER_REPOS: Dict[str, str] = {**{m["id"]: m["repo_id"] for m in WHISPER_CATALOG},
                                  "medium": "Systran/faster-whisper-medium", "small": "Systran/faster-whisper-small"}


def _whisper_model_dir(model_id: str) -> Path:
    if model_id not in _WHISPER_REPOS:
        raise ValueError(f"Неизвестная модель Whisper: {model_id!r}")
    return MODELS_DIR / "whisper" / model_id


def _hf_cache_dir(model_id: str) -> Optional[Path]:
    """The Hugging Face cache folder of exactly this model ("models--Systran--faster-whisper-large-v3"
    must not match ...-large-v3-turbo)."""
    repo = _WHISPER_REPOS.get(model_id)
    return MODELS_DIR / "whisper" / ("models--" + repo.replace("/", "--")) if repo else None


def dir_size_mb(d: Path) -> int:
    from app.paths import dir_size

    return int(dir_size(d) / 2**20)


COMPLETE_MARK = ".complete"  # written once every file of a model is downloaded


def _own_copy_complete(d: Path) -> bool:
    """A model downloaded by the app: all its files, not one cut off halfway (model.bin comes before
    the tokenizer, so model.bin alone is no proof). Older downloads have no mark: their files decide."""
    if not (d / "model.bin").exists():
        return False
    if (d / COMPLETE_MARK).exists():
        return True
    return (d / "config.json").exists() and (d / "tokenizer.json").exists() and not any(d.glob("*.part"))


def whisper_model_location(model_id: str) -> Optional[Path]:
    """The folder a downloaded Whisper model is in (our own layout or the Hugging Face cache)."""
    if model_id not in _WHISPER_REPOS:
        return None
    if _own_copy_complete(_whisper_model_dir(model_id)):
        return _whisper_model_dir(model_id)
    cache = _hf_cache_dir(model_id)
    return cache if cache and any(cache.glob("snapshots/*/model.bin")) else None


def is_whisper_model_installed(model_id: str) -> bool:
    if model_id not in _WHISPER_REPOS:
        return False
    if _own_copy_complete(_whisper_model_dir(model_id)):
        return True
    cache = _hf_cache_dir(model_id)  # the Hugging Face cache layout
    return bool(cache and any(cache.glob("snapshots/*/model.bin")))


def get_whisper_model_path(model_id: str) -> str:
    """
    Returns local folder path if model was downloaded via our manager,
    otherwise returns model_id so faster-whisper can resolve it.
    """
    if model_id in _WHISPER_REPOS and _own_copy_complete(_whisper_model_dir(model_id)):
        return str(_whisper_model_dir(model_id))
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
        where = whisper_model_location(item["id"]) if inst else None
        whisper_list.append({**item, "installed": inst, "path": str(where) if where else "",
                             "disk_mb": dir_size_mb(where) if where else 0})
    # GigaAM and Parakeet (app/asr_onnx.py) are chosen in the same list
    from app.asr_onnx import catalog as onnx_catalog

    for item in onnx_catalog():
        mb = item["size_mb"]
        size = f"{round(mb / 1024, 1)} GB" if mb >= 1000 else f"{mb} MB"
        from app.asr_onnx import model_dir

        where = model_dir(item["id"]) if item["installed"] else None
        whisper_list.append({**item, "size": size, "badge": f"{size} · {item['license']}", "vram": "CPU",
                             "path": str(where) if where else "", "disk_mb": dir_size_mb(where) if where else 0})

    diarizer_ready = is_diarizer_installed()
    any_speech_model = any_whisper_installed or any(i.get("installed") for i in whisper_list if i.get("engine") == "onnx")

    return {
        "needs_first_run_setup": not any_speech_model,
        "any_whisper_installed": any_whisper_installed,
        "active_whisper": active_whisper,
        "diarizer_installed": diarizer_ready,
        "whisper_catalog": whisper_list,
        "setup_state": dict(_setup_state),
    }


# Models are downloaded from a fixed commit, not from main: a rewritten or replaced repository does not
# reach anyone until the commit here is changed (and its license checked again)
ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_REVISION = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"


def _download_hf_repo_with_progress(
    repo_id: str,
    revision: str,
    target_dir: Path,
    label: str,
    base_pct: int,
    span_pct: int,
    on_progress: Optional[Callable[[float, float], None]] = None,
) -> None:
    """
    Downloads all required files from a HuggingFace repo directly via HTTP stream
    into target_dir with exact byte/MB progress tracking and zero symlink issues.
    on_progress(downloaded MB, total MB) after each piece; an exception it raises stops the download.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    api = huggingface_hub.HfApi()
    _log(f"Получение списка файлов репозитория {repo_id}...")
    info = api.repo_info(repo_id=repo_id, revision=revision, files_metadata=True)

    siblings = info.siblings or []
    files_to_get = []
    total_bytes = 0
    root = target_dir.resolve()
    for s in siblings:
        fname = s.rfilename
        if fname.startswith(".") or fname.endswith((".md", ".txt", ".png", ".jpg")):
            continue
        try:  # a name of the repository never leads out of the model's folder
            (root / fname).resolve().relative_to(root)
        except ValueError:
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

            url = huggingface_hub.hf_hub_url(repo_id=repo_id, filename=fname, revision=revision)
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
                        if on_progress:
                            on_progress(dl_mb, total_mb)

            if dest_file.exists():
                dest_file.unlink()
            tmp_file.rename(dest_file)
    (target_dir / COMPLETE_MARK).write_text(repo_id, encoding="utf-8")


def start_models_setup(
    whisper_model_id: str,
    install_diarizer: bool = True,
) -> Dict[str, Any]:
    pretty_name = format_whisper_model_name(whisper_model_id)
    with _setup_lock:
        if _setup_state["active"]:
            return dict(_setup_state)
        _setup_state.update(
            {
                "active": True,
                "model_id": whisper_model_id,
                "status": f"Подготовка к скачиванию {pretty_name}",
                "progress": 1,
                "downloaded_mb": 0.0,
                "total_mb": 0.0,
                "error": "",
                "logs": [],
            }
        )
        _log(f"Запуск установки модели {pretty_name}")

    def _worker():
        try:
            # 1. Download Whisper Model
            item = next((x for x in WHISPER_CATALOG if x["id"] == whisper_model_id), None)
            if not item:
                raise RuntimeError(f"Неизвестная модель Whisper: {whisper_model_id}")

            target_dir = _whisper_model_dir(whisper_model_id)
            _download_hf_repo_with_progress(
                repo_id=item["repo_id"],
                revision=item["revision"],
                target_dir=target_dir,
                label=item["name"],
                base_pct=2,
                span_pct=82 if install_diarizer else 96,
            )
            update_settings({"whisper_model": whisper_model_id})
            _log(f"Модель {item['name']} успешно установлена и выбрана по умолчанию!")

            # 2. Download Speaker Diarization Model (SpeechBrain ECAPA-TDNN)
            if install_diarizer and not is_diarizer_installed():
                _log("Загрузка модели разделения участников (ECAPA-TDNN)...")
                ecapa_dir = MODELS_DIR / "spkrec-ecapa-voxceleb"
                _download_hf_repo_with_progress(
                    repo_id=ECAPA_REPO,
                    revision=ECAPA_REVISION,
                    target_dir=ecapa_dir,
                    label="Модель разделения участников ECAPA-TDNN",
                    base_pct=85,
                    span_pct=14,
                )
                _log("Модель разделения участников установлена")

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


def ensure_whisper_model(model_id: str, on_progress: Optional[Callable[[float, float], None]] = None,
                         check_cancel: Optional[Callable[[], None]] = None) -> None:
    """A recording processed with a Whisper model that is not downloaded yet: the model is downloaded
    first, into the same folder and with the same progress in the settings as their Download button
    (faster-whisper would fetch it into its own cache, with no progress). Another download running
    (the settings, another recording) is waited for."""
    item = next((x for x in WHISPER_CATALOG if x["id"] == model_id), None)
    if not item or is_whisper_model_installed(model_id):
        return
    while True:
        with _setup_lock:
            if not _setup_state["active"]:
                _setup_state.update({"active": True, "model_id": model_id, "status": f"Подготовка к скачиванию {item['name']}",
                                     "progress": 1, "downloaded_mb": 0.0, "total_mb": 0.0, "error": "", "logs": []})
                break
        if check_cancel:
            check_cancel()
        time.sleep(1)
        if is_whisper_model_installed(model_id):
            return
    try:
        _log(f"Скачивание модели {item['name']} для обработки записи")
        _download_hf_repo_with_progress(repo_id=item["repo_id"], revision=item["revision"], target_dir=_whisper_model_dir(model_id), label=item["name"],
                                        base_pct=2, span_pct=97, on_progress=on_progress)
        _log(f"Модель {item['name']} скачана")
        _setup_state.update({"active": False, "progress": 100})
    except Exception as e:
        _setup_state.update({"active": False, "status": f"Ошибка загрузки: {e}", "error": str(e)})
        raise


def delete_whisper_model(model_id: str) -> bool:
    """Deletes exactly this model (only known ids: never a path from outside)."""
    if model_id not in _WHISPER_REPOS:
        raise ValueError(f"Неизвестная модель Whisper: {model_id!r}")
    deleted = False
    for d in (_whisper_model_dir(model_id), _hf_cache_dir(model_id)):
        if d and d.is_dir():
            # Renamed first: on Windows an open file stops the deletion halfway, and a half-deleted
            # folder would still look installed. The rename fails as a whole while a file is in use.
            gone = d.with_name(d.name + f".deleting-{datetime.now():%Y%m%d%H%M%S}")
            try:
                d.rename(gone)
            except OSError:
                raise RuntimeError("Файлы модели заняты (её использует программа) — закройте субтитры и повторите")
            shutil.rmtree(gone, ignore_errors=True)
            deleted = True
    return deleted
