"""
Model weights of the dub feature (models/dub) and its disk usage, for the Settings window.

The weights live in a Hugging Face cache under models/dub/hf (HF_HOME of the dub worker). They are
downloaded by the dub worker itself (the main app has no torch/chatterbox/demucs), on request from
Settings or on the first dub. Progress is measured by the bytes that appeared on disk.
"""

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import MODELS_DIR

DUB_MODELS_DIR = MODELS_DIR / "dub"
HF_HUB_DIR = DUB_MODELS_DIR / "hf" / "hub"

# Files that ChatterboxMultilingualTTS.from_pretrained (0.1.7) and demucs get_model("htdemucs") fetch
DUB_WEIGHTS: List[Dict[str, Any]] = [
    {
        "id": "chatterbox",
        "name": "Chatterbox Multilingual",
        "repo": "ResembleAI/chatterbox",
        "files": ["ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json",
                  "conds.pt", "Cangjie5_TC.json"],
        "size_bytes": 3_200_000_000,
        "license": "MIT",
    },
    {
        "id": "demucs",
        "name": "Demucs htdemucs",
        "repo": "adefossez/HTDemucs",
        "files": ["htdemucs.yaml"],
        "any_suffix": ".safetensors",
        "size_bytes": 84_000_000,
        "license": "MIT",
    },
]
_BY_ID = {w["id"]: w for w in DUB_WEIGHTS}


def _repo_dir(repo: str) -> Path:
    return HF_HUB_DIR / ("models--" + repo.replace("/", "--"))


def dir_size(path: Path) -> int:
    """Bytes under `path` without following links."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _linked_blobs(repo_dir: Path) -> Dict[str, int]:
    """Files a HF repo folder uses: its own files plus the blobs its links point to. Recent
    huggingface_hub versions keep the data in shared blobs (hub/blobs/xx/<sha>) behind links."""
    found: Dict[str, int] = {}
    for root, _dirs, files in os.walk(repo_dir, followlinks=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                real = os.path.realpath(path)
                found[os.path.normcase(real)] = os.stat(real).st_size
            except OSError:
                continue
    return found


def repo_size(repo: str) -> int:
    d = _repo_dir(repo)
    return sum(_linked_blobs(d).values()) if d.exists() else 0


def _weight_installed(w: Dict[str, Any]) -> bool:
    snaps = _repo_dir(w["repo"]) / "snapshots"
    if not snaps.is_dir():
        return False
    for snap in snaps.iterdir():
        if not all((snap / f).exists() for f in w["files"]):
            continue
        if w.get("any_suffix") and not any(p.suffix == w["any_suffix"] for p in snap.iterdir()):
            continue
        return True
    return False


# ---------------------------------------------------------------- download state
_lock = threading.Lock()
_state: Dict[str, Any] = {"active": False, "models": [], "progress": 0, "error": "", "done": False}
_cancel = threading.Event()


def weights_status() -> Dict[str, Any]:
    items = []
    for w in DUB_WEIGHTS:
        items.append({
            "id": w["id"], "name": w["name"], "repo": w["repo"], "license": w["license"],
            "installed": _weight_installed(w),
            "size_bytes": repo_size(w["repo"]),
            "expected_bytes": w["size_bytes"],
        })
    with _lock:
        st = dict(_state)
    return {"items": items, "download": st, "dir": str(DUB_MODELS_DIR)}


def _download(ids: List[str]) -> None:
    from app.dubbing import DubWorker

    repos = [_BY_ID[i] for i in ids]
    start = sum(repo_size(w["repo"]) for w in repos)
    need = max(1, sum(w["size_bytes"] for w in repos) - start)
    hub_start = dir_size(HF_HUB_DIR) if HF_HUB_DIR.exists() else 0
    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(1.0):
            # Blobs are written under hub/blobs while downloading; the repo links appear at the end
            have = dir_size(HF_HUB_DIR) - hub_start if HF_HUB_DIR.exists() else 0
            have += start
            pct = int(min(99, max(1, 100 * (have - start) / need)))
            with _lock:
                _state["progress"] = pct

    def _check_cancel() -> None:
        if _cancel.is_set():
            raise RuntimeError("Загрузка отменена")

    threading.Thread(target=_watch, daemon=True).start()
    worker = None
    try:
        worker = DubWorker(_check_cancel, purpose="download")
        worker.call({"cmd": "download", "models": ids})
        with _lock:
            _state.update(active=False, progress=100, done=True, error="")
    except Exception as e:
        with _lock:
            _state.update(active=False, done=False, error=str(e).splitlines()[0][:300] if str(e) else "Ошибка загрузки")
    finally:
        stop.set()
        if worker is not None:
            worker.close()


def start_download(ids: Optional[List[str]], dub_job_running: bool) -> Dict[str, Any]:
    from app.dub_setup import is_installed

    if not is_installed():
        return {**weights_status(), "error": "Сначала установите окружение дубляжа"}
    if dub_job_running:
        return {**weights_status(), "error": "Идёт дубляж — дождитесь его окончания"}
    wanted = [i for i in (ids or [w["id"] for w in DUB_WEIGHTS]) if i in _BY_ID]
    wanted = [i for i in wanted if not _weight_installed(_BY_ID[i])]
    if not wanted:
        return weights_status()
    with _lock:
        if _state["active"]:
            return weights_status()
        _cancel.clear()
        _state.update(active=True, models=wanted, progress=1, error="", done=False)
    threading.Thread(target=_download, args=(wanted,), daemon=True, name="dub_weights").start()
    return weights_status()


def cancel_download() -> Dict[str, Any]:
    _cancel.set()
    return weights_status()


def delete_weights(model_id: str, dub_job_running: bool) -> Dict[str, Any]:
    w = _BY_ID.get(model_id)
    if not w:
        raise ValueError(f"Неизвестная модель {model_id!r}")
    with _lock:
        busy = _state["active"]
    if busy or dub_job_running:
        return {**weights_status(), "error": "Модели сейчас используются — удалите их после дубляжа или загрузки"}
    repo_dir = _repo_dir(w["repo"])
    blobs = set(_linked_blobs(repo_dir)) if repo_dir.exists() else set()
    shutil.rmtree(repo_dir, ignore_errors=True)
    lock_dir = HF_HUB_DIR / ".locks" / ("models--" + w["repo"].replace("/", "--"))
    shutil.rmtree(lock_dir, ignore_errors=True)
    # Shared blobs are freed only when no other model links to them
    still_used = set()
    for other in HF_HUB_DIR.glob("models--*"):
        still_used |= set(_linked_blobs(other))
    hub = os.path.normcase(str(HF_HUB_DIR.resolve()))
    for blob in blobs - still_used:
        if blob.startswith(hub + os.sep):
            for f in (blob, blob + ".refs"):
                try:
                    os.remove(f)
                except OSError:
                    pass
    return weights_status()


# ---------------------------------------------------------------- disk usage
_env_size_cache: Dict[str, Any] = {"key": None, "bytes": None, "at": 0.0}


def disk_usage(venv: Path, dubs_dir: Path) -> Dict[str, Any]:
    """Sizes of the environment, the weights and the dub outputs. The venv size (~6 GB, tens of
    thousands of files) is cached until the environment changes."""
    marker = venv / "verbascribe-dub-ready.txt"
    key = (venv.exists(), marker.stat().st_mtime if marker.exists() else 0)
    if _env_size_cache["key"] != key or time.time() - _env_size_cache["at"] > 3600:
        _env_size_cache.update(key=key, bytes=dir_size(venv) if venv.exists() else 0, at=time.time())
    try:
        free = shutil.disk_usage(DUB_MODELS_DIR if DUB_MODELS_DIR.exists() else MODELS_DIR.parent).free
    except OSError:
        free = None
    return {
        "env_bytes": _env_size_cache["bytes"],
        "weights_bytes": dir_size(DUB_MODELS_DIR) if DUB_MODELS_DIR.exists() else 0,
        "outputs_bytes": dir_size(dubs_dir) if dubs_dir.exists() else 0,
        "free_bytes": free,
    }
