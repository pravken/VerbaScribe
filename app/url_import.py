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
MAX_BYTES = 8 * 2**30  # a link to more than this (a day-long video, a disk image) is not downloaded

_cancel: Dict[int, threading.Event] = {}  # downloads running, by recording


class _Cancelled(Exception):
    pass


def cancel(meeting_id: int) -> bool:
    """Stops the download of this recording; False when none runs."""
    ev = _cancel.get(meeting_id)
    if ev is None:
        return False
    ev.set()
    return True


def ensure_package() -> None:
    """Installs yt-dlp when it is missing: the version and file pinned in asr_onnx.PINNED."""
    try:
        importlib.import_module("yt_dlp")
        return
    except ImportError:
        pass
    from app import asr_onnx

    asr_onnx.install_pinned(PACKAGE)


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


def _drop_files(stem: str) -> None:
    """The file (and the .part pieces) of a download nobody needs any more."""
    for p in UPLOADS_DIR.glob(f"{stem}_*"):
        try:
            p.unlink()
        except OSError:
            pass


def _download(meeting_id: int, url: str) -> None:
    from app.database import get_meeting, update_meeting_fields, update_meeting_progress
    from app.ffmpeg_manager import find_tool
    from app.transcriber import enqueue_meeting_job

    stem = uuid.uuid4().hex[:8]
    stop = _cancel.setdefault(meeting_id, threading.Event())
    stop.clear()
    try:
        ensure_package()
        import yt_dlp

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        last = {"pct": 0}

        def hook(d: Dict[str, Any]) -> None:
            if stop.is_set():
                raise _Cancelled()
            if (d.get("downloaded_bytes") or 0) > MAX_BYTES:
                raise RuntimeError(f"файл больше {MAX_BYTES // 2**30} ГБ")
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
            "socket_timeout": 30,
            "max_filesize": MAX_BYTES,
        }
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and (info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming")):
                raise RuntimeError("это прямая трансляция: она не кончается, скачать её целиком нельзя")
            size = (info or {}).get("filesize") or (info or {}).get("filesize_approx") or 0
            if size > MAX_BYTES:
                raise RuntimeError(f"файл {size / 2**30:.1f} ГБ, больше {MAX_BYTES // 2**30} ГБ")
            if stop.is_set():
                raise _Cancelled()
            info = ydl.process_ie_result(info, download=True)
        if info and info.get("entries"):
            info = next((e for e in info["entries"] if e), info)
        downloads = (info or {}).get("requested_downloads") or []
        path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
        if not path or not path.is_file():
            found = sorted(UPLOADS_DIR.glob(f"{stem}_*"))
            path = found[0] if found else None
        if not path:
            raise RuntimeError("Файл не скачался")
        if not get_meeting(meeting_id):  # the recording was deleted while it was downloading
            _drop_files(stem)
            return
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
        _drop_files(stem)
        if stop.is_set() or isinstance(e, _Cancelled) or isinstance(getattr(e, "exc_info", [None, None])[1], _Cancelled):
            update_meeting_progress(meeting_id, status="cancelled", progress=0, stage_text="Скачивание отменено",
                                    error_message="")
            return
        traceback.print_exc()
        update_meeting_progress(meeting_id, status="failed", progress=0, stage_text="",
                                error_message=f"Не удалось скачать по ссылке: {str(e)[:250]}")
    finally:
        _cancel.pop(meeting_id, None)
