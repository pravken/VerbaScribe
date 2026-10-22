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
current one): the database in use is kept as meetings.before-restore-<date>.db. Media files are put
back where they were, never over an existing file.
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
FORMAT = 1


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


def restore(src: Path) -> Dict[str, Any]:
    """Stages the copy's database for the next start and puts the media files back now."""
    with zipfile.ZipFile(src) as z:
        manifest = _check(z)
        with tempfile.TemporaryDirectory(prefix="vs_restore_") as tmp:
            db = Path(z.extract("meetings.db", tmp))
            conn = sqlite3.connect(str(db))
            try:
                n = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("База в копии повреждена")
            except sqlite3.DatabaseError:
                raise ValueError("База в копии повреждена")
            finally:
                conn.close()
            shutil.copyfile(db, PENDING)
        if "bench.json" in z.namelist() and not (DATA_DIR / "bench.json").exists():
            (DATA_DIR / "bench.json").write_bytes(z.read("bench.json"))
        restored = skipped = 0
        for f in manifest.get("media") or []:
            target = Path(str(f.get("path") or ""))
            name = str(f.get("name") or "")
            if not target.name or name not in z.namelist() or ".." in Path(name).parts:
                continue
            if target.exists():
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            restored += 1
    return {"recordings": n, "media_restored": restored, "media_kept": skipped, "created": manifest.get("created", ""),
            "restart_needed": True}


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
    for suffix in ("-wal", "-shm"):
        Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
    PENDING.replace(DB_PATH)
    print(f"[backup] restored the library from a backup; the previous database is {kept.name}", flush=True)
    return str(kept)
