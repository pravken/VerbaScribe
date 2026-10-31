"""
Filesystem locations used by Verbaliox.

User data (database, uploads, extracted audio, exports, the session token, a downloaded FFmpeg)
lives in a per-user folder outside the program directory, so the app folder can be replaced,
updated or kept read-only:

    Windows: %LOCALAPPDATA%\\Verbaliox
    other:   $XDG_DATA_HOME/Verbaliox or ~/.local/share/Verbaliox

`VERBALIOX_DATA_DIR` overrides the location. Speech models stay next to the program
(`models/`, override with `VERBALIOX_MODELS_DIR`): they are large, re-downloadable and shared
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
from typing import Any, Dict, List, Optional, Tuple

APP_DIR = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = APP_DIR / "data"
MOVED_NOTE_NAME = "MOVED.txt"


def _default_data_dir(name: str = "Verbaliox") -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / name


# The program was called VerbaScribe before: its data folder is renamed once (migrate_renamed_data_dir)
FORMER_NAME = "VerbaScribe"


def _is_packaged_python() -> bool:
    r"""
    True for Python from the Microsoft Store (an MSIX-packaged process). Windows silently redirects
    such a process's writes to %LOCALAPPDATA% into its private package folder
    (...\Packages\PythonSoftwareFoundation.Python.*\LocalCache), which other programs such as
    FFmpeg and Explorer cannot see and which is deleted together with that Python.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return rc != 15700  # APPMODEL_ERROR_NO_PACKAGE: a regular, unpackaged process
    except Exception:
        return "WindowsApps" in (getattr(sys, "_base_executable", "") or sys.executable)


PACKAGED_PYTHON = _is_packaged_python()
_ENV_DATA_DIR = os.environ.get("VERBALIOX_DATA_DIR")
# Under a packaged (Store) Python, %LOCALAPPDATA% is virtualized, so data stays next to the program
DATA_DIR = Path(
    _ENV_DATA_DIR or (LEGACY_DATA_DIR if PACKAGED_PYTHON else _default_data_dir())
).expanduser().resolve()
MODELS_DIR = Path(os.environ.get("VERBALIOX_MODELS_DIR") or (APP_DIR / "models")).expanduser().resolve()


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


# Set when both the old <app>/data and the current data folder contain recordings
LEGACY_CONFLICT: Optional[Dict[str, Any]] = None


def _count_recordings(db_path: Path) -> int:
    """Number of recordings in a Verbaliox database; 0 if it is empty, unreadable or not ours."""
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def migrate_legacy_data_dir(legacy: Optional[Path] = None, target: Optional[Path] = None) -> bool:
    """
    Moves `<app>/data` to DATA_DIR once. Safe against interruption: everything is copied into a
    staging folder first, verified (same files, same sizes), renamed into place, database paths are
    rewritten, and only then the old folder is removed. A MOVED.txt note is left at the old place.
    Returns True if a migration happened.
    """
    legacy = (legacy or LEGACY_DATA_DIR).resolve()
    target = (target or DATA_DIR).resolve()

    global LEGACY_CONFLICT
    if legacy == target or not (legacy / "meetings.db").is_file():
        return False
    if (target / "meetings.db").exists():
        legacy_count = _count_recordings(legacy / "meetings.db")
        target_count = _count_recordings(target / "meetings.db")
        if target_count == 0 and legacy_count > 0:
            # The new folder was created first (e.g. the app was started once from another copy)
            # and holds only an empty library: keep it as a backup and move the real data in.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for suffix in ("", "-wal", "-shm"):
                db_file = target / f"meetings.db{suffix}"
                if db_file.exists():
                    db_file.rename(target / f"meetings.empty-{stamp}.db{suffix}")
            _log(f"{target} had an empty database; kept it as meetings.empty-{stamp}.db")
        else:
            # Both have recordings (e.g. data was copied back by hand). Never merge or overwrite
            # silently; the UI shows a warning with both folders.
            if legacy_count > 0:
                LEGACY_CONFLICT = {"legacy": str(legacy), "current": str(target), "legacy_recordings": legacy_count}
            _log(
                f"Legacy data folder {legacy} ({legacy_count} recordings) still exists, but {target} "
                f"already has a database ({target_count} recordings); not migrating."
            )
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
            "Verbaliox now keeps recordings, the database and exports in:\n"
            f"{target}\n\n"
            "Данные Verbaliox (записи, база, экспорт) теперь хранятся в папке выше.\n"
            "This folder can be deleted. / Эту папку можно удалить.\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    _log("Migration finished")
    return True


def _rewrite_moved_root(db_path: Path, old_root: Path, new_root: Path) -> int:
    """
    After the data folder was renamed: every stored path inside it (media, audio, dubs, exports,
    settings, also inside JSON) points to the new folder. Only whole folder names are matched.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        pairs = []
        for old, new in ((str(old_root), str(new_root)), (old_root.as_posix(), new_root.as_posix())):
            for sep in ("\\", "/"):
                pairs.append((old + sep, new + sep))
                pairs.append(((old + sep).replace("\\", "\\\\"), (new + sep).replace("\\", "\\\\")))  # in JSON
        pairs = list(dict.fromkeys(pairs))
        changed = 0
        rows = conn.execute("SELECT name, COALESCE(sql, '') FROM sqlite_master WHERE type = 'table'").fetchall()
        virtual = [name for name, sql in rows if sql.upper().startswith("CREATE VIRTUAL")]
        # Full-text indexes and their shadow tables are left alone: they follow the triggers
        tables = [name for name, _ in rows if name not in virtual and not name.startswith("sqlite_")
                  and not any(name.startswith(v + "_") for v in virtual)]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')
                    if not r[2] or any(t in r[2].upper() for t in ("CHAR", "TEXT", "CLOB"))]
            for col in cols:
                for old, new in pairs:
                    cur = conn.execute(f'UPDATE "{table}" SET "{col}" = REPLACE("{col}", ?, ?) '
                                       f'WHERE instr("{col}", ?) > 0', (old, new, old))
                    changed += cur.rowcount
                for old, new in ((str(old_root), str(new_root)), (old_root.as_posix(), new_root.as_posix())):
                    cur = conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE "{col}" = ?', (new, old))
                    changed += cur.rowcount
        conn.commit()
        return changed
    finally:
        conn.close()


def migrate_renamed_data_dir(old: Optional[Path] = None, target: Optional[Path] = None) -> bool:
    """
    Renames the data folder of the program's former name (%LOCALAPPDATA%\\VerbaScribe) to DATA_DIR
    once, on the same disk, so nothing is copied; then the paths stored in the database follow.
    A folder of the new name that already holds a library is never overwritten.
    Returns True if the folder was renamed. Raises OSError when Windows refuses (a file is open).
    """
    old = (old or _default_data_dir(FORMER_NAME)).resolve()
    target = (target or DATA_DIR).resolve()
    if old == target or not (old / "meetings.db").is_file():
        return False
    if (target / "meetings.db").exists() and _count_recordings(target / "meetings.db") > 0:
        _log(f"{old} (the former name) still exists, but {target} already has a library; not renaming")
        return False
    aside = None
    if target.exists():
        # Created before the rename (logs of this start, an empty library): kept aside
        aside = target.with_name(f"{target.name}.before-rename-{datetime.now():%Y%m%d-%H%M%S}")
        target.rename(aside)
    target.parent.mkdir(parents=True, exist_ok=True)
    old.rename(target)  # _log writes into DATA_DIR: nothing is logged before this line
    if aside:
        _log(f"{target} existed without recordings; kept it as {aside.name}")
    rewritten = _rewrite_moved_root(target / "meetings.db", old, target)
    _log(f"Data folder renamed {old} -> {target}, {rewritten} stored paths updated")
    return True


def _resolve_data_dir() -> Path:
    if os.environ.get("VERBALIOX_SKIP_MIGRATION") == "1":
        return DATA_DIR
    if PACKAGED_PYTHON and not _ENV_DATA_DIR:
        # An earlier version moved the data into %LOCALAPPDATA%, which this Python only sees
        # through its private virtualized copy: bring it back next to the program.
        virtual_dir = _default_data_dir(FORMER_NAME)  # what those versions were called
        try:
            if migrate_legacy_data_dir(legacy=virtual_dir, target=DATA_DIR):
                (DATA_DIR / MOVED_NOTE_NAME).unlink(missing_ok=True)
                _log("Data restored from the virtualized AppData folder of the Store Python")
        except Exception as e:
            _log(f"Restoring data from {virtual_dir} failed: {e}")
            if (virtual_dir / "meetings.db").is_file() and not (DATA_DIR / "meetings.db").is_file():
                return virtual_dir  # keep the library visible; the old folder is untouched
        return DATA_DIR
    if not _ENV_DATA_DIR:
        try:
            migrate_renamed_data_dir()
        except Exception as e:
            _log(f"Renaming the data folder of the former name failed: {e}")
            former = _default_data_dir(FORMER_NAME)
            if (former / "meetings.db").is_file() and not (DATA_DIR / "meetings.db").is_file():
                _log(f"Using {former} for now; the rename is tried again at the next start")
                return former
    try:
        migrate_legacy_data_dir()
    except Exception as e:
        _log(f"Migration failed: {e}")
        if (LEGACY_DATA_DIR / "meetings.db").is_file():
            # Keep working with the old folder rather than showing an empty library
            _log(f"Using the old data folder {LEGACY_DATA_DIR} for now")
            return LEGACY_DATA_DIR
    return DATA_DIR


# The sound and video files the app takes in (import, the watched folder, a backup's media); the same
# list as MEDIA_EXT of the page (static/js/library.js). ".ts" is left out: TypeScript files share it.
MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".3gp", ".wmv", ".flv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".amr", ".aif", ".aiff", ".mka",
})

DATA_DIR = _resolve_data_dir()
UPLOADS_DIR = DATA_DIR / "uploads"
AUDIO_DIR = DATA_DIR / "audio"
EXPORTS_DIR = DATA_DIR / "exports"
BIN_DIR = DATA_DIR / "bin"
# Recordings made in the app (the folder can be changed in the settings, see recordings_dir())
DEFAULT_RECORDINGS_DIR = DATA_DIR / "recordings"
THUMBS_DIR = DATA_DIR / "thumbs"
DB_PATH = DATA_DIR / "meetings.db"
SESSION_TOKEN_FILE = DATA_DIR / ".session_token"

for _d in (DATA_DIR, UPLOADS_DIR, AUDIO_DIR, EXPORTS_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def recordings_dir(configured: str = "") -> Path:
    """The folder for recordings made in the app: the one chosen in the settings if it can be
    used, otherwise <data>/recordings."""
    if configured:
        p = Path(configured).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                return p.resolve()
        except OSError:
            pass
    DEFAULT_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_RECORDINGS_DIR


def dir_size(path: Path) -> int:
    """Bytes in a folder. File links are followed and every file is counted once: a Hugging Face
    snapshot folder holds links to its blobs, the cache folder holds both."""
    seen = set()
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                st = os.stat(os.path.join(root, name))
            except OSError:
                continue  # a broken link, a file removed meanwhile
            key = (st.st_dev, st.st_ino)
            if st.st_ino and key in seen:
                continue
            seen.add(key)
            total += st.st_size
    return total
