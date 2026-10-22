"""
Auto-import: media files that appear in a chosen folder (a phone sync folder, a dictaphone, Zoom's
recordings folder) are added to the library in place and transcribed by themselves.

A file is taken once its size has stopped changing for STABLE_SEC (a copy or a recording still
being written is not grabbed half-way). Every file seen is remembered (watch_seen), so a recording
deleted from the library is not imported again.
"""

import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from app.database import _db_lock, get_all_settings, get_connection
from app.settings_schema import as_int

POLL_SEC = 15.0
STABLE_SEC = 20.0
MAX_FILES_PER_POLL = 20

_state: Dict[str, object] = {"running": False, "last_import": "", "error": "", "imported": 0}
_pending: Dict[str, Tuple[int, float]] = {}  # path -> (size, first time seen at that size)
_keys: Dict[str, str] = {}  # path as found -> its resolved form (the key of watch_seen), worked out once
_started = False
_lock = threading.Lock()


def _init_table() -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS watch_seen (path TEXT PRIMARY KEY, seen_at TEXT NOT NULL)")
        conn.commit()
        conn.close()


def _seen() -> Set[str]:
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT path FROM watch_seen").fetchall()
        conn.close()
    return {r["path"] for r in rows}


def _mark_seen(paths: List[str]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_connection()
        conn.executemany("INSERT OR IGNORE INTO watch_seen (path, seen_at) VALUES (?, ?)", [(p, now) for p in paths])
        conn.commit()
        conn.close()


def candidates(folder: Path, recursive: bool, extensions: Set[str]) -> List[Path]:
    """Media files in the folder. The names are filtered first: a folder like Documents has tens of
    thousands of files, and only media files are looked at (os.scandir reads the type with the name)."""
    out: List[Path] = []
    stack = [str(folder)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.name.startswith((".", "$")):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if recursive:
                                stack.append(e.path)
                        elif os.path.splitext(e.name)[1].lower() in extensions and e.is_file():
                            out.append(Path(e.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(out)


def _key(p: Path) -> str:
    raw = str(p)
    key = _keys.get(raw)
    if key is None:
        key = _keys[raw] = str(p.resolve())
    return key


def ready_files(files: List[Path], seen: Set[str], now: float) -> List[Path]:
    """New files whose size has not changed for STABLE_SEC."""
    out = []
    present = set()
    for p in files:
        key = _key(p)
        present.add(key)
        if key in seen:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        prev = _pending.get(key)
        if prev is None or prev[0] != size:
            _pending[key] = (size, now)
        elif size > 0 and now - prev[1] >= STABLE_SEC:
            out.append(p)
            _pending.pop(key, None)
    for gone in [k for k in _pending if k not in present]:  # deleted or moved away while being copied
        _pending.pop(gone, None)
    return out[:MAX_FILES_PER_POLL]


def scan_once(add: Callable[[Path], None], now: Optional[float] = None) -> int:
    from app.main import SUPPORTED_EXTENSIONS

    settings = get_all_settings()
    folder = (settings.get("watch_folder") or "").strip()
    if not folder:
        return 0
    root = Path(folder)
    if not root.is_dir():
        _state["error"] = f"Папка не найдена: {folder}"
        return 0
    _init_table()
    files = candidates(root, (settings.get("watch_folder_recursive") or "true") == "true", set(SUPPORTED_EXTENSIONS))
    seen = _seen()
    if settings.get("watch_folder_baseline") != folder:
        # A newly chosen folder: what is already there is not imported, only what comes next
        _mark_seen([_key(p) for p in files])
        from app.database import update_settings

        update_settings({"watch_folder_baseline": folder})
        return 0
    ready = ready_files(files, seen, now if now is not None else time.time())
    for p in ready:
        _mark_seen([_key(p)])  # before adding: a failure later in the batch must not import it twice
        add(p)
    if ready:
        _state["imported"] = int(_state["imported"]) + len(ready)
        _state["last_import"] = ready[-1].name
    _state["error"] = ""
    return len(ready)


def _add_file(p: Path) -> None:
    from app.main import _file_time, create_meeting, enqueue_meeting_job

    settings = get_all_settings()
    m = create_meeting(
        title=p.stem, filename=p.name, media_path=str(p.resolve()),
        language=settings.get("language", "auto"), model_name=settings.get("whisper_model", "large-v3-turbo"),
        num_speakers_hint=as_int(settings.get("num_speakers"), 0), source="local",
        recorded_at=_file_time(p), title_is_default=True,
    )
    enqueue_meeting_job(m["id"])
    print(f"[watch] imported {p}", flush=True)


def _run() -> None:
    while True:
        time.sleep(POLL_SEC)
        try:
            scan_once(_add_file)
        except Exception as e:
            traceback.print_exc()
            _state["error"] = str(e)[:300]


def start() -> None:
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_run, daemon=True, name="watch_folder").start()
            _started = True
            _state["running"] = True


def status() -> Dict[str, object]:
    return {**_state, "folder": get_all_settings().get("watch_folder") or ""}
