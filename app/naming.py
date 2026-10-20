"""
File names of a recording's files: its recording in the recordings folder and every export, made
from one template like the Obsidian notes ("{date} {title}" by default; {time} and {lang} too).
"""

from pathlib import Path
from typing import Any, Dict, Optional

from app.database import _db_lock, get_all_settings, get_connection, get_meeting

DEFAULT_TEMPLATE = "{date} {title}"


def base_name(m: Dict[str, Any], template: Optional[str] = None) -> str:
    """The file name without extension, e.g. "2026-10-20 Planning the release"."""
    from app.obsidian import meeting_datetime, safe_filename

    if template is None:
        template = get_all_settings().get("file_name_template") or DEFAULT_TEMPLATE
    template = template.strip() or DEFAULT_TEMPLATE
    dt = meeting_datetime(m)
    name = (template.replace("{date}", dt.strftime("%Y-%m-%d")).replace("{time}", dt.strftime("%H-%M"))
            .replace("{title}", m.get("title") or "").replace("{lang}", (m.get("language") or "").upper()))
    return safe_filename(name, limit=120)


def unique_path(path: Path, own: Optional[Path] = None) -> Path:
    """`path`, or "name (2).ext" and so on when another file has that name."""
    candidate, n = path, 2
    while candidate.exists() and (own is None or candidate.resolve() != own.resolve()):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def rename_recording_file(meeting_id: int) -> Optional[Path]:
    """Gives a recording made in the app the file name of the template (after its title changed).
    Files the user added are never renamed; a file in use stays as it is until the next change."""
    m = get_meeting(meeting_id)
    if not m or m.get("source") != "recording" or not m.get("owns_media") or not m.get("media_path"):
        return None
    old = Path(m["media_path"])
    if not old.is_file():
        return None
    new = unique_path(old.with_name(base_name(m) + old.suffix), own=old)
    if new == old:
        return old
    try:
        old.rename(new)
    except OSError as e:
        print(f"[naming] {old.name} not renamed: {e}", flush=True)
        return None
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE meetings SET media_path = ?, filename = ? WHERE id = ?", (str(new), new.name, meeting_id))
        conn.commit()
        conn.close()
    return new
