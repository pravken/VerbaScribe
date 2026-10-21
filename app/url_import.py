"""
Import by link: a video or podcast page (YouTube, VK Video, RuTube, a podcast episode, a direct
file link …) is downloaded with yt-dlp (Unlicense) and transcribed like an imported file.

yt-dlp is installed into the program's Python the first time it is needed (it is updated often,
so the newest version is taken). Only the sound is downloaded when the site offers it separately.
"""

import importlib
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.paths import UPLOADS_DIR

PACKAGE = "yt-dlp"


def ensure_package() -> None:
    """Installs yt-dlp when it is missing (uv first, then pip, as for onnx-asr)."""
    try:
        importlib.import_module("yt_dlp")
        return
    except ImportError:
        pass
    from app import asr_onnx

    print(f"[url] installing {PACKAGE}", flush=True)
    errors = []
    for cmd in asr_onnx._install_commands():
        cmd = cmd[:-1] + [PACKAGE]  # the same commands, for another package
        proc = asr_onnx._run(cmd)
        if proc.returncode == 0:
            importlib.invalidate_caches()
            return
        errors.append((proc.stderr or proc.stdout or "").strip()[-300:])
    raise RuntimeError("Не удалось установить yt-dlp: " + (errors[-1] if errors else "нет uv и pip"))


def valid_url(url: str) -> bool:
    u = urlparse((url or "").strip())
    return u.scheme in ("http", "https") and bool(u.netloc)


def start(url: str, model_name: str, language: str, num_speakers: int, folder_id: Optional[int] = None) -> Dict[str, Any]:
    """Creates the recording at once (shown as downloading) and downloads it in the background;
    then it goes into the transcription queue."""
    from app.database import create_meeting, get_meeting, update_meeting_progress

    if not valid_url(url):
        raise ValueError("Нужна ссылка вида https://…")
    host = urlparse(url).netloc.removeprefix("www.")
    m = create_meeting(title=host, filename=url[:300], media_path="", language=language, model_name=model_name,
                       num_speakers_hint=num_speakers, source="import", folder_id=folder_id, owns_media=True,
                       title_is_default=True)
    update_meeting_progress(m["id"], status="downloading", progress=1, stage_text="Скачивание по ссылке…")
    threading.Thread(target=_download, args=(m["id"], url), daemon=True, name=f"url_import_{m['id']}").start()
    return get_meeting(m["id"]) or m


def restart(meeting_id: int, url: str) -> None:
    from app.database import update_meeting_progress

    update_meeting_progress(meeting_id, status="downloading", progress=1, stage_text="Скачивание по ссылке…", error_message="")
    threading.Thread(target=_download, args=(meeting_id, url), daemon=True, name=f"url_import_{meeting_id}").start()


def _download(meeting_id: int, url: str) -> None:
    from app.database import update_meeting_fields, update_meeting_progress
    from app.ffmpeg_manager import find_tool
    from app.transcriber import enqueue_meeting_job

    try:
        ensure_package()
        import yt_dlp

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        stem = uuid.uuid4().hex[:8]
        last = {"pct": 0}

        def hook(d: Dict[str, Any]) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = int(90 * (d.get("downloaded_bytes") or 0) / total) if total else 0
                if pct >= last["pct"] + 3:
                    last["pct"] = pct
                    update_meeting_progress(meeting_id, progress=max(1, pct), stage_text=f"Скачивание по ссылке… {pct}%")

        ffmpeg = find_tool("ffmpeg")
        opts: Dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": str(UPLOADS_DIR / f"{stem}_%(title).80B.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "windowsfilenames": True,
        }
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if info and info.get("entries"):
            info = next((e for e in info["entries"] if e), info)
        downloads = (info or {}).get("requested_downloads") or []
        path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
        if not path or not path.is_file():
            found = sorted(UPLOADS_DIR.glob(f"{stem}_*"))
            path = found[0] if found else None
        if not path:
            raise RuntimeError("Файл не скачался")
        title = str((info or {}).get("title") or "").strip()[:300]
        date = str((info or {}).get("upload_date") or "")
        if title:
            update_meeting_fields(meeting_id, title=title)
        from app.database import _db_lock, get_connection

        with _db_lock:
            conn = get_connection()
            conn.execute("UPDATE meetings SET media_path = ?, filename = ?, recorded_at = COALESCE(NULLIF(?, ''), recorded_at) WHERE id = ?",
                         (str(path), path.name, f"{date[:4]}-{date[4:6]}-{date[6:8]} 12:00:00" if len(date) == 8 else "", meeting_id))
            conn.commit()
            conn.close()
        update_meeting_progress(meeting_id, status="queued", progress=0, stage_text="В очереди на распознавание")
        enqueue_meeting_job(meeting_id)
    except Exception as e:
        traceback.print_exc()
        update_meeting_progress(meeting_id, status="failed", progress=0, stage_text="",
                                error_message=f"Не удалось скачать по ссылке: {str(e)[:250]}")
