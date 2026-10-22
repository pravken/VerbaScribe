"""
A backup of the library in one .zip (Settings -> General -> Backup), and its restore.

In the copy:
- meetings.db: recordings, transcripts, analysis, notes, folders, series, promises, voice profiles,
  the dictionary and the settings, copied with SQLite's own backup (consistent while the app works);
  without the API keys of cloud models: they are encrypted for this Windows user and stay here;
- bench.json: the model comparisons;
- with "with media": the sound and video files the app keeps (recordings, imported copies), under
  media/, with the place each came from.

A restore puts the database aside and takes the copy's at the next start (the running app holds the
current one): the database in use is kept as meetings.before-restore-<date>.db. A copy is a file the
user may have been sent, so nothing in it decides where files go or where data is sent:
- media files are put back where they were only inside this computer's data folder or recordings
  folder (never over an existing file); a file that lived anywhere else goes to the recordings folder
  under RESTORED_FOLDER, and the restored database points to it;
- the settings that hold addresses, folders and the cloud choice (LOCAL_SETTINGS) stay this computer's,
  and so do the cloud API keys (they are moved into the restored database at the next start).
"""

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR, DB_PATH

PENDING = DATA_DIR / "meetings.restore-pending.db"
busy = 0  # a copy is being written or read (app/app_status.py: the app does not quit in the middle)
FORMAT = 1
RESTORED_FOLDER = "Восстановлено из копии"
# Where data is sent and where files are written: taken from this computer, not from the copy
LOCAL_SETTINGS = ("ollama_url", "llm_provider", "llm_cloud_provider", "llm_cloud_consent", "llm_base_url_openai_compat",
                  "calendar_ics", "recordings_dir", "export_dir", "obsidian_vault", "obsidian_subfolder",
                  "watch_folder", "watch_folder_baseline", "watch_folder_recursive")
MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}


def _media_files() -> List[Dict[str, Any]]:
    from app.database import _db_lock, get_connection

    with _db_lock:  # the library list leaves the paths out: read here
        conn = get_connection()
        rows = conn.execute("SELECT id, media_path, media_removed FROM meetings").fetchall()
        conn.close()
    out = []
    for m in rows:
        p = Path(str(m["media_path"] or ""))
        if m["media_removed"] or not p.name or not p.is_file():
            continue
        out.append({"id": m["id"], "path": str(p), "name": f"media/{m['id']}_{p.name}", "size": p.stat().st_size})
    return out


def estimate() -> Dict[str, Any]:
    """Sizes for the Settings card: the database alone, and with the media."""
    media = _media_files()
    return {"db_mb": round(DB_PATH.stat().st_size / 2**20, 1) if DB_PATH.exists() else 0,
            "media_mb": round(sum(f["size"] for f in media) / 2**20), "media_files": len(media)}


def make(dest: Path, with_media: bool = False) -> Dict[str, Any]:
    global busy
    busy += 1
    try:
        return _make(dest, with_media)
    finally:
        busy -= 1


def _make(dest: Path, with_media: bool) -> Dict[str, Any]:
    from app import __version__

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vs_backup_") as tmp:
        copy = Path(tmp) / "meetings.db"
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(copy))
        try:
            src.backup(dst)
            dst.execute("DROP TABLE IF EXISTS secrets")  # cloud API keys stay on this computer
            dst.commit()
            dst.execute("VACUUM")
        finally:
            dst.close()
            src.close()
        media = _media_files() if with_media else []
        manifest = {"format": FORMAT, "app": "VerbaScribe", "version": __version__,
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "media": [{k: f[k] for k in ("id", "path", "name")} for f in media]}
        part = dest.with_name(dest.name + ".partial")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            z.write(copy, "meetings.db")
            bench = DATA_DIR / "bench.json"
            if bench.is_file():
                z.write(bench, "bench.json")
            for f in media:  # audio and video are compressed already
                z.write(f["path"], f["name"], compress_type=zipfile.ZIP_STORED)
        part.replace(dest)
    return {"path": str(dest), "size_mb": round(dest.stat().st_size / 2**20, 1), "media_files": len(media)}


def _check(z: zipfile.ZipFile) -> Dict[str, Any]:
    try:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except (KeyError, ValueError):
        raise ValueError("Это не резервная копия VerbaScribe")
    if manifest.get("app") != "VerbaScribe" or "meetings.db" not in z.namelist():
        raise ValueError("Это не резервная копия VerbaScribe")
    if int(manifest.get("format") or 0) > FORMAT:
        raise ValueError("Копия сделана более новой версией программы: обновите VerbaScribe")
    return manifest


def _inside(path: Path, roots: List[Path]) -> bool:
    try:
        p = path.resolve()
    except OSError:
        return False
    return any(p == r or r in p.parents for r in roots)


def _media_target(original: str, zip_name: str, roots: List[Path], restored_dir: Path) -> Optional[Path]:
    """Where a media file of the copy goes on this computer, or None when it is not a media file."""
    name = Path(zip_name.replace("\\", "/")).name
    if not name or Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
        return None
    target = Path(original) if original else None
    if target is not None and target.name and target.is_absolute() and _inside(target, roots):
        return target
    return restored_dir / name  # "<id>_<file name>": unique per recording


def _keep_local_settings(conn: sqlite3.Connection) -> None:
    from app.database import get_all_settings

    here = get_all_settings()
    for key in LOCAL_SETTINGS:
        if key in here:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, here[key]))
        else:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def restore(src: Path) -> Dict[str, Any]:
    """Stages the copy's database for the next start and puts the media files back now."""
    global busy
    busy += 1
    try:
        return _restore(src)
    finally:
        busy -= 1


def _restore(src: Path) -> Dict[str, Any]:
    from app.database import get_all_settings
    from app.paths import recordings_dir

    rec_dir = recordings_dir(get_all_settings().get("recordings_dir") or "")
    roots = [DATA_DIR.resolve(), rec_dir.resolve()]
    restored_dir = rec_dir / RESTORED_FOLDER
    with zipfile.ZipFile(src) as z:
        manifest = _check(z)
        names = set(z.namelist())
        with tempfile.TemporaryDirectory(prefix="vs_restore_") as tmp:
            db = Path(z.extract("meetings.db", tmp))
            conn = sqlite3.connect(str(db))
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("База в копии повреждена")
                n = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
                known = {int(r[0]): str(r[1] or "") for r in conn.execute("SELECT id, media_path FROM meetings")}
                restored = skipped = 0
                for f in manifest.get("media") or []:
                    name = str(f.get("name") or "")
                    try:
                        mid = int(f.get("id"))
                    except (TypeError, ValueError):
                        continue
                    original = str(f.get("path") or "")
                    if name not in names or mid not in known or known[mid] != original:
                        continue  # only the files of the copy's own recordings, at the place its database names
                    target = _media_target(original, name, roots, restored_dir)
                    if target is None:
                        continue
                    if target.exists():
                        skipped += 1
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as fin, open(target, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    if str(target) != original:  # moved into the recordings folder: the app owns this copy
                        conn.execute("UPDATE meetings SET media_path = ?, owns_media = 1 WHERE id = ?", (str(target), mid))
                    restored += 1
                _keep_local_settings(conn)
                conn.execute("DROP TABLE IF EXISTS secrets")  # keys come from this computer (apply_pending)
                conn.commit()
            except sqlite3.DatabaseError:
                raise ValueError("База в копии повреждена")
            finally:
                conn.close()
            shutil.copyfile(db, PENDING)
        if "bench.json" in names and not (DATA_DIR / "bench.json").exists():
            (DATA_DIR / "bench.json").write_bytes(z.read("bench.json"))
    return {"recordings": n, "media_restored": restored, "media_kept": skipped, "created": manifest.get("created", ""),
            "restart_needed": True}


def _carry_secrets(old_db: Path, new_db: Path) -> None:
    """The cloud API keys of this computer (encrypted for this Windows user) into the restored database."""
    conn = sqlite3.connect(str(new_db))
    try:
        conn.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        if conn.execute("SELECT 1 FROM old.sqlite_master WHERE type = 'table' AND name = 'secrets'").fetchone():
            conn.execute("CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("INSERT OR REPLACE INTO secrets (name, value, updated_at) SELECT name, value, updated_at FROM old.secrets")
        conn.commit()
        conn.execute("DETACH DATABASE old")
    finally:
        conn.close()


def apply_pending() -> Optional[str]:
    """At start, before the database is opened: a restored copy replaces it (the old one is kept)."""
    if not PENDING.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = DATA_DIR / f"meetings.before-restore-{stamp}.db"
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))  # the last changes may still be in the -wal file
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        shutil.copyfile(DB_PATH, kept)
        try:
            _carry_secrets(kept, PENDING)
        except sqlite3.DatabaseError as e:
            print(f"[backup] the API keys were not carried over: {e}", flush=True)
    for suffix in ("-wal", "-shm"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
    PENDING.replace(DB_PATH)
    print(f"[backup] restored the library from a backup; the previous database is {kept.name}", flush=True)
    return str(kept)
