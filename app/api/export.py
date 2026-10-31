"""Exports, folders, thumbnails, file details, recordings folder, Obsidian."""

import json
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from app import paths
from app.paths import THUMBS_DIR
from app.database import get_all_settings, get_meeting, update_settings
from app.export_build import build_export_bytes

from fastapi import APIRouter

from app.main import (  # noqa: E402 - shared helpers live in app/main.py
    _default_export_dir, _local_time, _pick_path_native, _recordings_dir,
)

router = APIRouter()

# --- Multi-format Export ---
def _build_export_bytes(
    m: Dict[str, Any], fmt: str, lang: Optional[str] = None, variant: str = "translated",
    opts: Optional[Dict[str, Any]] = None,
) -> tuple[bytes, str, str]:
    try:
        return build_export_bytes(m, fmt, lang, variant, opts)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/meetings/{meeting_id}/export/{fmt}")
def api_export_meeting(meeting_id: int, fmt: str, lang: Optional[str] = None, variant: str = "translated",
                       times: bool = True, names: bool = True, nofill: bool = False) -> Response:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    content, media_type, filename = _build_export_bytes(m, fmt, lang, variant, {"times": times, "names": names, "nofill": nofill})
    encoded_filename = quote(filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
    }
    return Response(content=content, media_type=media_type, headers=headers)

@router.post("/api/export-dir/pick")
def api_pick_export_dir() -> Dict[str, Any]:
    """Lets the user choose the default export folder via a native folder dialog."""
    chosen = _pick_path_native("folder", _default_export_dir())
    if not chosen:
        return {"ok": False, "cancelled": True}
    update_settings({"export_dir": chosen})
    return {"ok": True, "folder": chosen}

@router.get("/api/export-dir")
def api_get_export_dir() -> Dict[str, Any]:
    return {"folder": str(_default_export_dir())}

@router.delete("/api/ollama/models/{name:path}")
def api_delete_ollama_model(name: str) -> Dict[str, Any]:
    from app.ollama_client import delete_ollama_model, get_ollama_status
    from app.transcriber import running_job_kind

    if running_job_kind() in ("summary", "translate", "dub"):
        raise HTTPException(status_code=409, detail="Модель сейчас используется — удалите её после задачи")
    try:
        delete_ollama_model(name.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])
    return get_ollama_status()

class RevealPayload(BaseModel):
    path: str

@router.post("/api/reveal")
def api_reveal_path(payload: RevealPayload) -> Dict[str, Any]:
    """Highlights a saved file (or opens a folder) in Windows Explorer."""
    import subprocess

    try:
        p = Path(payload.path).resolve(strict=True)  # a full path without "..": Explorer is never handed a relative one
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        if p.is_dir():
            subprocess.Popen(["explorer.exe", str(p)])
        else:
            subprocess.Popen(["explorer.exe", f"/select,{p}"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}

# --- Library folders ---
@router.get("/api/folders")
def api_list_folders() -> List[Dict[str, Any]]:
    from app.database import list_folders

    return list_folders()

class FolderCreatePayload(BaseModel):
    name: str
    parent_id: Optional[int] = None

@router.post("/api/folders")
def api_create_folder(payload: FolderCreatePayload) -> Dict[str, Any]:
    from app.database import create_folder

    try:
        return create_folder(payload.name, payload.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/api/folders/{folder_id}")
def api_update_folder(folder_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Body: {"name"?: str, "parent_id"?: int | null}; a missing key leaves that field as it is."""
    from app.database import update_folder

    kwargs: Dict[str, Any] = {}
    if "name" in payload:
        kwargs["name"] = str(payload.get("name") or "")
    if "parent_id" in payload:
        pid = payload.get("parent_id")
        kwargs["parent_id"] = None if pid is None else int(pid)
    if "local_only" in payload:
        kwargs["local_only"] = bool(payload.get("local_only"))
    try:
        return update_folder(folder_id, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/folders/{folder_id}")
def api_delete_folder(folder_id: int) -> Dict[str, Any]:
    from app.database import delete_folder

    try:
        delete_folder(folder_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}

class MeetingFolderPayload(BaseModel):
    folder_id: Optional[int] = None

@router.patch("/api/meetings/{meeting_id}/folder")
def api_move_meeting(meeting_id: int, payload: MeetingFolderPayload) -> Dict[str, Any]:
    from app.database import set_meeting_folder

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    try:
        set_meeting_folder(meeting_id, payload.folder_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "folder_id": payload.folder_id}

# --- Video thumbnails for the library ---
_thumb_lock = threading.Lock()

@router.get("/api/meetings/{meeting_id}/thumb")
def api_meeting_thumb(meeting_id: int) -> Response:
    """A small frame of the video (made once with FFmpeg and kept in <data>/thumbs)."""
    from app.ffmpeg_manager import tool_cmd

    m = get_meeting(meeting_id)
    if not m or not m.get("is_video"):
        raise HTTPException(status_code=404, detail="Нет видео")
    media = Path(m.get("media_path") or "")
    thumb = THUMBS_DIR / f"{meeting_id}.jpg"
    headers = {"Cache-Control": "private, max-age=86400"}
    if thumb.is_file() and media.is_file() and thumb.stat().st_mtime >= media.stat().st_mtime:
        return FileResponse(str(thumb), media_type="image/jpeg", headers=headers)
    if not media.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    with _thumb_lock:  # several thumbnails at once would start an FFmpeg each
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        duration = float(m.get("duration") or 0.0)
        at = min(30.0, duration * 0.1) if duration > 1 else 0.0
        tmp = thumb.with_suffix(".tmp.jpg")
        for seek in (at, 0.0):
            cmd = [tool_cmd("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{seek:.2f}", "-i", str(media),
                   "-frames:v", "1", "-vf", "scale=160:-2", "-q:v", "5", str(tmp)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                break
            if tmp.is_file() and tmp.stat().st_size > 0:
                tmp.replace(thumb)
                return FileResponse(str(thumb), media_type="image/jpeg", headers=headers)
        tmp.unlink(missing_ok=True)
    raise HTTPException(status_code=404, detail="Не удалось сделать кадр")

# --- File details (the "Details" tab) ---
def _ffprobe_details(path: Path) -> Dict[str, Any]:
    from app.ffmpeg_manager import tool_cmd

    try:
        proc = subprocess.run(
            [tool_cmd("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        info = json.loads(proc.stdout or "{}")
    except Exception:
        return {}
    fmt = info.get("format") or {}
    out: Dict[str, Any] = {
        "container": fmt.get("format_long_name") or fmt.get("format_name") or "",
        "bitrate_kbps": round(int(fmt.get("bit_rate") or 0) / 1000) or None,
        "duration": float(fmt.get("duration") or 0.0) or None,
        "created": ((fmt.get("tags") or {}).get("creation_time") or ""),
    }
    for st in info.get("streams") or []:
        if st.get("codec_type") == "video" and not (st.get("disposition") or {}).get("attached_pic") and "video" not in out:
            fps = 0.0
            try:
                num, den = (st.get("avg_frame_rate") or "0/1").split("/")
                fps = round(float(num) / float(den), 2) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                pass
            out["video"] = {"codec": st.get("codec_name") or "", "width": st.get("width"), "height": st.get("height"), "fps": fps or None}
        elif st.get("codec_type") == "audio" and "audio" not in out:
            out["audio"] = {
                "codec": st.get("codec_name") or "", "sample_rate": int(st.get("sample_rate") or 0) or None,
                "channels": st.get("channels"), "layout": st.get("channel_layout") or "",
                "bitrate_kbps": round(int(st.get("bit_rate") or 0) / 1000) or None,
            }
    return out

def _file_entry(path_str: str) -> Optional[Dict[str, Any]]:
    if not path_str:
        return None
    p = Path(path_str)
    entry: Dict[str, Any] = {"path": str(p), "name": p.name, "exists": p.is_file()}
    if entry["exists"]:
        st = p.stat()
        entry.update(size=st.st_size, modified=_local_time(st.st_mtime), created=_local_time(getattr(st, "st_birthtime", st.st_ctime)))
    return entry

@router.get("/api/meetings/{meeting_id}/info")
def api_meeting_info(meeting_id: int) -> Dict[str, Any]:
    from app.database import get_folder

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    media = _file_entry(m.get("media_path") or "")
    words = sum(len((s.get("text") or "").split()) for s in m.get("segments") or [])
    chain = []
    f = get_folder(m["folder_id"]) if m.get("folder_id") else None
    while f and len(chain) < 20:
        chain.insert(0, {"id": f["id"], "name": f["name"], "system_key": f.get("system_key")})
        f = get_folder(f["parent_id"]) if f.get("parent_id") else None
    return {
        "id": m["id"],
        "title": m.get("title"),
        "filename": m.get("filename"),
        "source": m.get("source"),
        "owns_media": bool(m.get("owns_media")),
        "media": media,
        "audio": _file_entry(m.get("audio_path") or ""),
        "probe": _ffprobe_details(Path(m["media_path"])) if media and media["exists"] else {},
        "recorded_at": m.get("recorded_at") or "",
        "created_at": m.get("created_at") or "",
        "completed_at": m.get("completed_at") or "",
        "duration": m.get("duration") or 0.0,
        "status": m.get("status"),
        "language": m.get("language"),
        "model_name": m.get("model_name"),
        "transcript_model": m.get("transcript_model") or "",  # the model of the text shown (as in the header)
        "speakers": len(m.get("speakers") or {}),
        "segments": len(m.get("segments") or []),
        "words": words,
        "translations": sorted((m.get("translations") or {}).keys()),
        "dubs": [_file_entry(d.get("path") or "") for d in m.get("dubs") or []],
        "summaries": sorted(k for k, v in (m.get("summaries") or {}).items() if v and k != "error"),
        "folders": chain,
        "obsidian": _file_entry(m.get("obsidian_path") or ""),
        "pending_exports": m.get("pending_exports") or [],
    }

# --- Folder for recordings made in the app ---
@router.get("/api/recordings-dir")
def api_get_recordings_dir() -> Dict[str, Any]:
    return {"folder": str(_recordings_dir()), "default": str(paths.DEFAULT_RECORDINGS_DIR)}

@router.post("/api/recordings-dir/pick")
def api_pick_recordings_dir() -> Dict[str, Any]:
    chosen = _pick_path_native("folder", _recordings_dir())
    if not chosen:
        return {"ok": False, "cancelled": True}
    try:
        Path(chosen).mkdir(parents=True, exist_ok=True)
        probe = Path(chosen) / f".verbaliox-write-test-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"В эту папку нельзя записывать: {e}")
    update_settings({"recordings_dir": chosen})
    return {"ok": True, "folder": str(_recordings_dir())}

@router.get("/api/watch-folder")
def api_watch_folder_status() -> Dict[str, Any]:
    from app import watch_folder

    return watch_folder.status()

@router.post("/api/watch-folder/pick")
def api_watch_folder_pick() -> Dict[str, Any]:
    """Auto-import (app/watch_folder.py): files that appear in this folder are added by themselves."""
    current = get_all_settings().get("watch_folder") or ""
    chosen = _pick_path_native("folder", Path(current) if current and Path(current).is_dir() else Path.home())
    if not chosen:
        return {"ok": False, "cancelled": True}
    update_settings({"watch_folder": chosen})
    from app import watch_folder

    return {"ok": True, **watch_folder.status()}

@router.post("/api/watch-folder/off")
def api_watch_folder_off() -> Dict[str, Any]:
    update_settings({"watch_folder": "", "watch_folder_baseline": ""})
    from app import watch_folder

    return {"ok": True, **watch_folder.status()}

@router.post("/api/recordings-dir/reset")
def api_reset_recordings_dir() -> Dict[str, Any]:
    update_settings({"recordings_dir": ""})
    return {"ok": True, "folder": str(_recordings_dir())}

# --- Obsidian ---
@router.post("/api/obsidian/pick-vault")
def api_pick_obsidian_vault() -> Dict[str, Any]:
    current = (get_all_settings().get("obsidian_vault") or "").strip()
    chosen = _pick_path_native("folder", Path(current) if current and Path(current).is_dir() else Path.home())
    if not chosen:
        return {"ok": False, "cancelled": True}
    update_settings({"obsidian_vault": chosen})
    return {"ok": True, "folder": chosen, "is_vault": (Path(chosen) / ".obsidian").is_dir()}

@router.get("/api/obsidian/status")
def api_obsidian_status() -> Dict[str, Any]:
    from app.obsidian import vault_dir

    settings = get_all_settings()
    vault = (settings.get("obsidian_vault") or "").strip()
    target = vault_dir(settings)
    return {
        "vault": vault,
        "exists": bool(vault) and Path(vault).is_dir(),
        "is_vault": bool(vault) and (Path(vault) / ".obsidian").is_dir(),
        "folder": str(target) if target else "",
    }

@router.post("/api/meetings/{meeting_id}/obsidian")
def api_export_obsidian(meeting_id: int) -> Dict[str, Any]:
    """Saves (or updates) the recording's note in the Obsidian vault."""
    from app.obsidian import export_meeting

    try:
        path = export_meeting(meeting_id, generate=True)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось записать заметку: {e}")
    return {"ok": True, "path": str(path), "filename": path.name, "folder": str(path.parent)}

# --- Exports that may need a job first (see app/pending_exports.py) ---
class ExportRequestPayload(BaseModel):
    kind: str = "doc"  # doc | video_soft | video_burn | dub_media | dub_srt | obsidian
    fmt: str = ""
    lang: Optional[str] = None
    variant: str = "translated"
    opts: Optional[Dict[str, Any]] = None  # times, names, nofill of the export menu
    model: Optional[str] = None

@router.post("/api/meetings/{meeting_id}/export-request")
def api_export_request(meeting_id: int, payload: ExportRequestPayload) -> Dict[str, Any]:
    from app.pending_exports import request

    try:
        return request(meeting_id, payload.model_dump(exclude={"model"}), model=(payload.model or "").strip() or None)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.delete("/api/meetings/{meeting_id}/pending-exports")
def api_cancel_pending_exports(meeting_id: int) -> Dict[str, Any]:
    from app.pending_exports import cancel_all

    cancel_all(meeting_id)
    return {"ok": True}

@router.post("/api/vram/unload-all")
def api_vram_unload_all() -> Dict[str, Any]:
    """Frees every model the app holds now (the same as the idle timeout)."""
    from app import idle_unload
    from app.vram import vram_snapshot

    if idle_unload._busy():
        raise HTTPException(status_code=409, detail="Сейчас идёт обработка или запись")
    res = idle_unload.unload_now()
    return {**vram_snapshot(), **res}
