import asyncio
import hmac
import os
import re
import secrets
import shutil
import sys
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.paths import APP_DIR as BASE_DIR, SESSION_TOKEN_FILE, UPLOADS_DIR, recordings_dir
from app.settings_schema import as_int
from app.database import (
    create_meeting,
    get_all_settings,
    get_meeting,
    init_db,
    is_local_only,
    list_meetings,
    save_meeting_results,  # noqa: F401 - reached as app.main.save_meeting_results
    update_meeting_progress,
    update_settings,
)
from app.export_build import EXPORT_VARIANTS, default_export_dir
from app.transcriber import (
    enqueue_meeting_job,
    is_job_active,
)

_SPEAKER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _ensure_not_transcribing(meeting_id: int) -> None:
    """Edits of the transcript would be overwritten by a recognition job that is queued or running."""
    from app.transcriber import is_transcribing

    if is_transcribing(meeting_id):
        raise HTTPException(status_code=409, detail="Запись распознаётся — правки станут доступны после окончания")


def _ensure_not_processing(meeting_id: int) -> None:
    if is_job_active(meeting_id):
        raise HTTPException(status_code=409, detail="Запись уже обрабатывается. Дождитесь окончания или отмените обработку.")

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".flac",
    ".aac",
    ".wma",
}

# --- Local session security ---
# The server only listens on localhost, but any web page open in a browser can still send
# requests to 127.0.0.1. Every launch therefore gets a random session token: the desktop
# window opens /?token=..., receives it as an HttpOnly SameSite=Strict cookie, and every
# other request without that cookie is rejected. Requests with a foreign Host header
# (DNS rebinding) are rejected as well, and so are requests other pages send (_foreign_page):
# the cookie is sent along from any port of 127.0.0.1.
SESSION_COOKIE = "vs_session"


def _cookie_name(request: Request) -> str:
    # Browsers share cookies between ports of one host, so two instances (or another local app)
    # on 127.0.0.1 would overwrite each other's session cookie without the port in its name.
    return f"{SESSION_COOKIE}_{request.url.port or 80}"
SESSION_TOKEN = os.environ.get("VERBASCRIBE_TOKEN") or secrets.token_urlsafe(32)
SESSION_TOKEN_FILE.write_text(SESSION_TOKEN, encoding="utf-8")

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"} | {
    h.strip().lower() for h in os.environ.get("VERBASCRIBE_ALLOWED_HOSTS", "").split(",") if h.strip()
}
_PUBLIC_PREFIXES = ("/static/",)

_LOCKED_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>VerbaScribe</title></head>
<body style="font-family:Segoe UI,sans-serif;background:#141416;color:#f5f5f7;padding:48px">
<h2>VerbaScribe</h2>
<p>Откройте программу через VerbaScribe.exe или start.bat — эта страница доступна только из окна приложения.</p>
<p style="color:#98989d">Open the app via VerbaScribe.exe or start.bat — this page is only available from the app window.</p>
<script>
// Inside the app window this means the window opened with an outdated session token (the server
// was still starting): ask the window for a fresh address and reload. Only once: if that address
// is refused too (the server belongs to another Windows user), reloading would never end.
window.addEventListener('pywebviewready', async () => {
  try {
    if (sessionStorage.getItem('vs_relocked')) return;
    const url = await window.pywebview.api.app_url();
    if (url && url.indexOf('token=') > 0) { sessionStorage.setItem('vs_relocked', '1'); location.replace(url); }
  } catch (e) {}
});
</script>
</body></html>"""


def _host_allowed(host_header: str) -> bool:
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    else:
        host = host.split(":")[0]
    return host in _ALLOWED_HOSTS


def _foreign_page(request: Request) -> bool:
    """A request sent by another page of the browser. The cookie alone does not tell: for the browser
    every port of 127.0.0.1 is the same site, so a page of another local program (a dev server, a
    notebook) gets the session cookie sent along. Browsers say who sent a request in Sec-Fetch-Site
    (and Origin); requests without both (not from a browser page) still need the cookie."""
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return origin.rstrip("/").lower() != f"{request.url.scheme}://{request.headers.get('host', '')}".lower()


def _token_valid(value: Optional[str]) -> bool:
    return bool(value) and hmac.compare_digest(value, SESSION_TOKEN)


def _is_proactor_connection_reset(context: Dict[str, Any]) -> bool:
    return isinstance(context.get("exception"), ConnectionResetError) and "_call_connection_lost" in repr(
        context.get("handle") or context.get("message") or ""
    )


def _quiet_windows_connection_resets() -> None:
    """
    On Windows, when the browser drops a keep-alive or SSE connection, asyncio's Proactor loop logs
    "Exception in callback _ProactorBasePipeTransport._call_connection_lost ... [WinError 10054]".
    The socket is already gone and nothing is lost, so only that exact case is silenced.
    """
    if sys.platform != "win32":
        return
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        if _is_proactor_connection_reset(context):
            return
        if previous:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _quiet_windows_connection_resets()
    init_db()
    from app.database import retry_pending_deletes

    try:
        retry_pending_deletes()  # files that were open when their recording was deleted
    except Exception:
        traceback.print_exc()
    from app import idle_unload

    try:
        recover_orphan_recordings()
    except Exception:
        traceback.print_exc()
    threading.Thread(target=_retention_loop, daemon=True, name="retention").start()
    try:
        from app.model_manager import remove_retired_models

        remove_retired_models()
    except Exception:
        traceback.print_exc()

    idle_unload.start()
    from app import onnx_gpu

    onnx_gpu.maybe_install_on_start()
    from app import semantic

    semantic.start_indexer()
    from app import call_watch

    call_watch.start()
    from app import watch_folder

    watch_folder.start()
    # Resume recordings that were interrupted mid-processing
    active_states = {"queued", "extracting_audio", "transcribing", "diarizing", "summarizing"}
    for m in list_meetings():
        if m.get("status") in active_states:
            enqueue_meeting_job(int(m["id"]))
        elif m.get("status") == "dubbing":
            # A dub interrupted by a restart is simply dropped; the transcript itself is complete
            update_meeting_progress(int(m["id"]), status="completed", progress=100, stage_text="Дубляж прерван перезапуском")
        elif m.get("status") == "downloading":
            update_meeting_progress(int(m["id"]), status="failed", progress=0, stage_text="",
                                    error_message="Скачивание по ссылке прервано перезапуском — нажмите «Повторить»")
        elif m.get("status") in ("translating", "rendering"):
            # Partial translations are kept and can be completed with "Update translation"
            update_meeting_progress(int(m["id"]), status="completed", progress=100, stage_text="Задача прервана перезапуском")
    yield


app = FastAPI(
    title="VerbaScribe",
    version=__version__,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def local_session_guard(request: Request, call_next):
    if not _host_allowed(request.headers.get("host", "")):
        return Response("Forbidden host", status_code=403)

    path = request.url.path
    if not path.startswith(_PUBLIC_PREFIXES) and _foreign_page(request):
        return Response('{"detail":"Forbidden origin"}', status_code=403, media_type="application/json")
    if path == "/":
        query_token = request.query_params.get("token")
        if _token_valid(query_token):
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(_cookie_name(request), SESSION_TOKEN, httponly=True, samesite="strict", path="/")
            return resp
        if not _token_valid(request.cookies.get(_cookie_name(request))):
            return HTMLResponse(_LOCKED_PAGE, status_code=401)
        return await call_next(request)

    if path.startswith(_PUBLIC_PREFIXES):
        resp = await call_next(request)
        # Revalidate (ETag) so an updated page script/styles.css is never served from a stale cache
        resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    if not _token_valid(request.cookies.get(_cookie_name(request))):
        return Response('{"detail":"Unauthorized"}', status_code=401, media_type="application/json")
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>UI loading...</h1>")
    from app.page import render

    return HTMLResponse(
        render(STATIC_DIR),  # index.html with static/parts/*.html in place
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            # Everything is served locally; the page may only talk to this server.
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


def api_get_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return {**m, "local_only_effective": is_local_only(m)}


def _recordings_dir() -> Path:
    return recordings_dir(get_all_settings().get("recordings_dir") or "")


def _recording_folder() -> Path:
    """Where a new studio recording goes: the recordings folder, or today's folder in it."""
    from datetime import datetime

    from app.naming import in_date_folder

    return in_date_folder(_recordings_dir(), datetime.now())


# Recording stops by itself below this much free space, so the file and the database stay intact
DISK_STOP_BYTES = 300 * 1024 * 1024
DISK_WARN_BYTES = 2 * 1024 * 1024 * 1024


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return -1


def disk_state() -> Dict[str, Any]:
    """Free space where recordings are written (the recordings folder and the data folder, which
    holds the system-audio capture)."""
    rec, data = _recordings_dir(), UPLOADS_DIR
    free = [b for b in (_free_bytes(rec), _free_bytes(data)) if b >= 0]
    low = min(free) if free else -1
    return {"free_bytes": low, "recordings_dir": str(rec), "warn_bytes": DISK_WARN_BYTES,
            "stop_bytes": DISK_STOP_BYTES, "low": 0 <= low < DISK_WARN_BYTES, "critical": 0 <= low < DISK_STOP_BYTES}


def _local_time(ts: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _file_time(path: Path) -> str:
    """When a media file was made: the modification time survives copying, the creation time does not."""
    try:
        return _local_time(path.stat().st_mtime)
    except OSError:
        return ""


def screen_mix_command(ffmpeg: str, screen: Path, audio: List[Path], dest: Path) -> List[str]:
    """The picture recorded by the app with the sound of the page (microphone) and/or the system:
    the video is copied as recorded, the sound sources are mixed into one AAC track."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(screen)]
    for a in audio:
        cmd += ["-i", str(a)]
    if not audio:
        maps = ["-map", "0:v"]
    elif len(audio) == 1:
        maps = ["-map", "0:v", "-map", "1:a"]
    else:
        maps = ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:normalize=0[a]", "-map", "0:v", "-map", "[a]"]
    return cmd + maps + ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest)]


def _part_target(part: Path, title: str) -> Path:
    """Where a recording part is kept as a file of its own (".page_x.webm.part" -> ".webm")."""
    name = part.name[:-5] if part.name.endswith(".part") else part.name
    ext = Path(name).suffix or ".bin"
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title)[:100]
    return _recording_folder() / f"{uuid.uuid4().hex[:8]}_{safe}{ext}"


def _queue_file(title: str, dest: Path, has_video: Optional[bool]) -> Dict[str, Any]:
    settings = get_all_settings()
    m = create_meeting(
        title=title[:300], filename=dest.name, media_path=str(dest),
        language=settings.get("language", "auto"),
        model_name=settings.get("whisper_model", "large-v3-turbo"),
        num_speakers_hint=as_int(settings.get("num_speakers"), 0),
        source="recording", owns_media=True, title_is_default=True, has_video=has_video,
    )
    from app.naming import rename_recording_file

    rename_recording_file(m["id"])
    enqueue_meeting_job(m["id"])
    return get_meeting(m["id"]) or m


def _salvage_parts(title: str, parts: List[Tuple[Path, Optional[bool]]], notes: str = "") -> Dict[str, Any]:
    """The parts of a recording could not be joined: each one is kept as a recording of its own,
    so nothing that was recorded is lost."""
    created = []
    for part, has_video in parts:
        if not part.is_file() or part.stat().st_size == 0:
            continue
        dest = _part_target(part, title)
        shutil.move(str(part), str(dest))
        created.append(_queue_file(title, dest, has_video))
        if notes.strip():
            from app.database import update_meeting_fields

            update_meeting_fields(created[-1]["id"], user_notes=notes[:50000])
    if not created:
        raise HTTPException(status_code=500, detail="Не удалось свести запись, и её части пусты")
    return {"created": created, "salvaged": True}


def _retention_loop() -> None:
    """Privacy setting "keep the sound N days": checked at start and every six hours."""
    import time as _time

    from app.database import expired_media_ids, remove_media

    while True:
        try:
            days = int(get_all_settings().get("media_retention_days") or 0)
            for mid in expired_media_ids(days):
                if not is_job_active(mid):
                    remove_media(mid)
        except Exception:
            traceback.print_exc()
        try:  # 16 kHz WAVs of recordings processed by older versions (see transcriber._drop_work_audio)
            from app.transcriber import drop_old_work_audio

            drop_old_work_audio()
        except Exception:
            traceback.print_exc()
        _time.sleep(6 * 3600)


def recover_orphan_recordings() -> int:
    """At start: parts of recordings that were never finished (the app closed or crashed while
    recording) become recordings, instead of staying hidden in the folders."""
    found: List[Tuple[Path, Optional[bool]]] = []
    try:
        rec = _recordings_dir()
        found += [(p, None) for p in rec.glob(".page_*.webm.part")]
        found += [(p, True) for p in rec.glob(".screen_*.mkv.part")]
        found += [(p, False) for p in UPLOADS_DIR.glob("system_*.wav") if p.stat().st_size > 4096]
    except OSError:
        return 0
    n = 0
    for part, has_video in found:
        if part.stat().st_size < 4096:
            part.unlink(missing_ok=True)
            continue
        try:
            _salvage_parts("Восстановленная запись", [(part, has_video)])
            n += 1
        except Exception:
            traceback.print_exc()
    if n:
        print(f"[record] recovered {n} unfinished recording part(s)", flush=True)
    return n


def record_mix_command(ffmpeg: str, page: Optional[Path], system: Optional[Path], has_video: bool,
                       has_mic: bool, dest: Path) -> Optional[List[str]]:
    """The FFmpeg command that turns the parts of a recording into one file, or None when the page
    recording can be used as it is."""
    base = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if page and not system:
        # Video from MediaRecorder has no index: a copy into a new file makes it seekable
        return base + ["-i", str(page), "-c", "copy", str(dest)] if has_video else None
    if system and not page:
        return base + ["-i", str(system), "-c:a", "aac", "-b:a", "160k", str(dest)]
    if has_video:
        # The picture is copied as recorded; the sound is the system audio, mixed with the microphone
        audio = (["-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[a]", "-map", "0:v", "-map", "[a]"]
                 if has_mic else ["-map", "0:v", "-map", "1:a"])
        return base + ["-i", str(page), "-i", str(system), *audio, "-c:v", "copy", "-c:a", "libopus", "-b:a", "128k", str(dest)]
    # normalize=0: each source keeps its level instead of both being halved
    return base + ["-i", str(page), "-i", str(system), "-filter_complex",
                   "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[a]", "-map", "[a]",
                   "-c:a", "aac", "-b:a", "160k", str(dest)]


def _live_target(translate_to: Optional[str], pair: Optional[List[str]]) -> tuple:
    """Validated (target, pair) of live captions; "auto" translates each line into the other
    language of the pair."""
    from app.translation import TEXT_LANGUAGES

    target = (translate_to or "").strip().lower() or None
    if target and target != "auto" and target not in TEXT_LANGUAGES:
        raise HTTPException(status_code=400, detail="Неподдерживаемый язык перевода")
    clean = [str(p).strip().lower() for p in (pair or []) if str(p).strip()][:2]
    if clean and (len(clean) != 2 or clean[0] == clean[1] or any(p not in TEXT_LANGUAGES for p in clean)):
        raise HTTPException(status_code=400, detail="Для автоперевода нужны два разных языка")
    return target, clean or None


_EXPORT_VARIANTS = EXPORT_VARIANTS


_EXPORT_FILE_TYPES = {
    "md": ("Markdown (*.md)", "*.md", ".md"),
    "txt": ("Текстовый файл (*.txt)", "*.txt", ".txt"),
    "srt": ("Субтитры (*.srt)", "*.srt", ".srt"),
    "vtt": ("Субтитры WebVTT (*.vtt)", "*.vtt", ".vtt"),
    "json": ("JSON (*.json)", "*.json", ".json"),
    "docx": ("Документ Word (*.docx)", "*.docx", ".docx"),
    "mp4": ("Видео MP4 (*.mp4)", "*.mp4", ".mp4"),
    "mkv": ("Видео MKV (*.mkv)", "*.mkv", ".mkv"),
    "m4a": ("Аудио M4A (*.m4a)", "*.m4a", ".m4a"),
}


def _default_export_dir() -> Path:
    return default_export_dir()


def _remember_export(meeting_id: int, path: Path) -> None:
    """The folder is offered next time; the file is listed with the recording (deleted with it on request)."""
    from app.database import add_export_path

    update_settings({"export_dir": str(_export_base(path.parent))})
    add_export_path(meeting_id, str(path))


def _export_base(folder: Path) -> Path:
    from app.naming import without_date_folders

    return without_date_folders(folder)


def _pick_export_path(m: Optional[Dict[str, Any]], filename: str, fmt: str) -> Optional[str]:
    """The Save dialog for an export, opened in the recording's date folder when that is on (a
    date folder made just for this and left empty after a cancel is removed)."""
    from app.naming import in_date_folder
    from app.obsidian import meeting_datetime

    base = _default_export_dir()
    folder = in_date_folder(base, meeting_datetime(m)) if m else base
    existed = folder == base or any(folder.iterdir())
    chosen = _pick_path_native("save", folder, filename, fmt)
    if not chosen and not existed:
        try:
            folder.rmdir()
        except OSError:
            pass
    return chosen


def _pick_path_native(mode: str, directory: Path, filename: str = "", fmt: str = "") -> Optional[str]:
    """
    Spawns an isolated native file/folder dialog via app.native_dialog in a subprocess.
    This avoids thread-affinity problems and deadlocks with pywebview or uvicorn.
    """
    from app import native_dialog

    desc, pat, ext = _EXPORT_FILE_TYPES.get(fmt.lower(), ("All files (*.*)", "*.*", ""))
    filter_arg = f"{desc}|{pat}|Все файлы (*.*)|*.*" if ext else "Все файлы (*.*)|*.*"
    return native_dialog.run([
        "--mode", mode, "--dir", str(directory), "--filename", filename, "--filter", filter_arg, "--ext", ext,
        "--title", "Сохранить как" if mode == "save" else "Выбор папки для экспорта",
    ])


# --- The endpoints, one router per part of the program, in the order they were written (the order of
# routes matters: "/api/meetings/search" before "/api/meetings/{meeting_id}") ---
from app.api import analysis, cloud, export, library, media, recording, system  # noqa: E402

for _part in (system, library, recording, analysis, media, export, cloud):
    app.include_router(_part.router)
