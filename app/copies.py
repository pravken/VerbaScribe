"""
The saved copies of a recording, listed in «Сведения о записи»: the sound and video files the app
saved from it (without fillers and pauses, clean sound, key moments, the sound as MP3/WAV, the
voice dubs). Exports remember their paths (database.add_export_path); the media files among them
are the copies. Deleting a copy removes its file and forgets its path; the recording is untouched.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

MEDIA_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".mkv", ".webm", ".mov"}


def copy_id(path: str) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def copy_kind(path: Path) -> str:
    """What the copy is, from the name the export gave it (api/media.py)."""
    stem = path.stem.lower()
    for suffix, kind in ((".clean-sound", "denoise"), (".highlights", "highlights"), (".clean", "clean")):
        if stem.endswith(suffix):
            return kind
    if path.suffix.lower() in (".mp3", ".wav"):
        return "audio"
    return "media"


def _entry(path: str, kind: Optional[str] = None, lang: str = "") -> Dict[str, Any]:
    p = Path(path)
    exists = p.is_file()
    st = p.stat() if exists else None
    return {
        "id": copy_id(path),
        "name": p.name,
        "path": str(p),
        "kind": kind or copy_kind(p),
        "lang": lang,
        "exists": exists,
        "size": st.st_size if st else None,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds") if st else "",
        "deletable": kind != "dub",
    }


def list_copies(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The media files saved from the recording, newest first; the dubs after them."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for path in reversed(m.get("exports") or []):
        if not isinstance(path, str) or Path(path).suffix.lower() not in MEDIA_SUFFIXES or path in seen:
            continue
        seen.add(path)
        out.append(_entry(path))
    for d in m.get("dubs") or []:
        path = str(d.get("path") or "")
        if path and path not in seen:
            seen.add(path)
            out.append(_entry(path, "dub", str(d.get("lang") or "")))
    return out


def find_copy(m: Dict[str, Any], cid: str) -> Optional[Dict[str, Any]]:
    return next((c for c in list_copies(m) if c["id"] == cid), None)


def forget_export_path(meeting_id: int, path: str) -> None:
    from app.database import _db_lock, _loads_list, get_connection

    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT exports_json FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if row is not None:
            items = [p for p in _loads_list(row[0]) if p != path]
            conn.execute("UPDATE meetings SET exports_json = ? WHERE id = ?", (json.dumps(items, ensure_ascii=False), meeting_id))
            conn.commit()
        conn.close()


def delete_copy(m: Dict[str, Any], cid: str) -> bool:
    """Removes a saved copy (its file and its line); the voice dubs are removed with the dub itself."""
    c = find_copy(m, cid)
    if not c or not c["deletable"]:
        return False
    Path(c["path"]).unlink(missing_ok=True)
    forget_export_path(int(m["id"]), c["path"])
    return True
