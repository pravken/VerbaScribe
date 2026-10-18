"""
Filesystem locations used by VerbaScribe.

User data (database, uploads, extracted audio, exports, the session token, a downloaded FFmpeg)
lives in a per-user folder outside the program directory, so the app folder can be replaced,
updated or kept read-only:

    Windows: %LOCALAPPDATA%\\VerbaScribe
    other:   $XDG_DATA_HOME/VerbaScribe or ~/.local/share/VerbaScribe

`VERBASCRIBE_DATA_DIR` overrides the location. Speech models stay next to the program
(`models/`, override with `VERBASCRIBE_MODELS_DIR`): they are large, re-downloadable and shared
by all users of the install.

Earlier versions kept user data in `<app>/data`. On the first start with the new layout that folder
is moved to the new location once (see `migrate_legacy_data_dir`).
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

APP_DIR = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = APP_DIR / "data"
MOVED_NOTE_NAME = "MOVED.txt"


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "VerbaScribe"


DATA_DIR = Path(os.environ.get("VERBASCRIBE_DATA_DIR") or _default_data_dir()).expanduser().resolve()
MODELS_DIR = Path(os.environ.get("VERBASCRIBE_MODELS_DIR") or (APP_DIR / "models")).expanduser().resolve()


def _log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(f"[data] {msg}", flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "migration.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _file_inventory(root: Path) -> List[Tuple[str, int]]:
    return sorted(
        (str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file()
    )


def _rewrite_db_paths(db_path: Path, old_root: Path, new_root: Path) -> int:
    """Points media/audio paths that lived inside the old data folder to the new one."""
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()}
        if not cols:
            return 0
        changed = 0
        for mid, media_path, audio_path in conn.execute(
            "SELECT id, media_path, COALESCE(audio_path, '') FROM meetings"
        ).fetchall():
            updates = {}
            for key, value in (("media_path", media_path), ("audio_path", audio_path)):
                if value and Path(value).is_absolute():
                    try:
                        rel = Path(value).resolve().relative_to(old_root)
                    except ValueError:
                        continue
                    updates[key] = str(new_root / rel)
            if updates:
                conn.execute(
                    f"UPDATE meetings SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                    (*updates.values(), mid),
                )
                changed += 1
        conn.commit()
        return changed
    finally:
        conn.close()


def migrate_legacy_data_dir(legacy: Optional[Path] = None, target: Optional[Path] = None) -> bool:
    """
    Moves `<app>/data` to DATA_DIR once. Safe against interruption: everything is copied into a
    staging folder first, verified (same files, same sizes), renamed into place, database paths are
    rewritten, and only then the old folder is removed. A MOVED.txt note is left at the old place.
    Returns True if a migration happened.
    """
    legacy = (legacy or LEGACY_DATA_DIR).resolve()
    target = (target or DATA_DIR).resolve()

    if legacy == target or not (legacy / "meetings.db").is_file():
        return False
    if (target / "meetings.db").exists():
        # Both exist (e.g. data was copied back by hand). Never merge or overwrite silently.
        _log(f"Legacy data folder {legacy} still exists, but {target} already has a database; not migrating.")
        return False

    staging = target.with_name(target.name + ".migrating")
    if staging.exists():
        shutil.rmtree(staging)  # leftover from an interrupted earlier attempt (the source is intact)
    target.parent.mkdir(parents=True, exist_ok=True)

    _log(f"Moving user data {legacy} -> {target}")
    # The session token is per launch and must not be carried over
    shutil.copytree(legacy, staging, ignore=shutil.ignore_patterns(".session_token", MOVED_NOTE_NAME))
    src_inv = [f for f in _file_inventory(legacy) if Path(f[0]).name not in (".session_token", MOVED_NOTE_NAME)]
    if _file_inventory(staging) != src_inv:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("Не удалось перенести данные: копия не совпадает с оригиналом. Старая папка не тронута.")

    if target.exists():
        # The folder may already exist without a database (e.g. created by a previous run's logs)
        for item in target.iterdir():
            dest = staging / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
        shutil.rmtree(target)
    staging.rename(target)

    rewritten = _rewrite_db_paths(target / "meetings.db", legacy, target)
    _log(f"Copied {len(src_inv)} files, updated paths of {rewritten} recordings")

    shutil.rmtree(legacy, ignore_errors=True)
    try:
        legacy.mkdir(exist_ok=True)
        (legacy / MOVED_NOTE_NAME).write_text(
            "VerbaScribe now keeps recordings, the database and exports in:\n"
            f"{target}\n\n"
            "Данные VerbaScribe (записи, база, экспорт) теперь хранятся в папке выше.\n"
            "This folder can be deleted. / Эту папку можно удалить.\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    _log("Migration finished")
    return True


def _resolve_data_dir() -> Path:
    if os.environ.get("VERBASCRIBE_SKIP_MIGRATION") == "1":
        return DATA_DIR
    try:
        migrate_legacy_data_dir()
    except Exception as e:
        _log(f"Migration failed: {e}")
        if (LEGACY_DATA_DIR / "meetings.db").is_file():
            # Keep working with the old folder rather than showing an empty library
            _log(f"Using the old data folder {LEGACY_DATA_DIR} for now")
            return LEGACY_DATA_DIR
    return DATA_DIR


DATA_DIR = _resolve_data_dir()
UPLOADS_DIR = DATA_DIR / "uploads"
AUDIO_DIR = DATA_DIR / "audio"
EXPORTS_DIR = DATA_DIR / "exports"
BIN_DIR = DATA_DIR / "bin"
DB_PATH = DATA_DIR / "meetings.db"
SESSION_TOKEN_FILE = DATA_DIR / ".session_token"

for _d in (DATA_DIR, UPLOADS_DIR, AUDIO_DIR, EXPORTS_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
