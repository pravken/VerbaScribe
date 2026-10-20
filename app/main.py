import asyncio
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import __version__, events, paths
from app.paths import APP_DIR as BASE_DIR, DATA_DIR, MODELS_DIR, SESSION_TOKEN_FILE, THUMBS_DIR, UPLOADS_DIR, recordings_dir
from app.database import (
    add_dictionary_term,
    create_meeting,
    delete_dictionary_term,
    delete_meeting,
    get_all_settings,
    get_meeting,
    init_db,
    is_local_only,
    list_dictionary,
    list_meetings,
    save_meeting_results,
    toggle_dictionary_term,
    update_meeting_fields,
    update_meeting_progress,
    update_settings,
)
from app.export_build import EXPORT_VARIANTS, build_export_bytes, default_export_dir
from app.ffmpeg_manager import ffmpeg_status, start_ffmpeg_download
from app.hardware import get_hardware_info
from app.ollama_client import generate_meeting_summary, get_ollama_status, start_model_pull
from app.text_utils import realign_words
from app.transcriber import (
    enqueue_burn_subtitles_job,
    enqueue_dub_job,
    enqueue_meeting_job,
    enqueue_translate_job,
    is_job_active,
    request_cancel,
)

_SPEAKER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


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
# (DNS rebinding) are rejected as well.
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
// was still starting): ask the window for a fresh address and reload.
window.addEventListener('pywebviewready', async () => {
  try { const url = await window.pywebview.api.app_url(); if (url && url.indexOf('token=') > 0) location.replace(url); } catch (e) {}
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
    from app import idle_unload

    try:
        from app.model_manager import remove_retired_models

        remove_retired_models()
    except Exception:
        traceback.print_exc()

    idle_unload.start()
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
    return HTMLResponse(
        index_file.read_text(encoding="utf-8"),
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


# --- System, Hardware & Settings ---
@app.get("/api/system")
def api_system_info() -> Dict[str, Any]:
    from app.model_manager import get_models_status

    return {
        "hardware": get_hardware_info(),
        "settings": get_all_settings(),
        "ollama": get_ollama_status(),
        "models": get_models_status(),
        "ffmpeg": ffmpeg_status(),
        "paths": {
            "data": str(DATA_DIR), "models": str(MODELS_DIR), "legacy_conflict": paths.LEGACY_CONFLICT,
            "recordings": str(_recordings_dir()), "uploads": str(UPLOADS_DIR),
            "recordings_default": str(paths.DEFAULT_RECORDINGS_DIR),
        },
    }


@app.get("/api/ffmpeg/status")
def api_ffmpeg_status() -> Dict[str, Any]:
    return ffmpeg_status()


@app.post("/api/ffmpeg/install")
def api_ffmpeg_install() -> Dict[str, Any]:
    """Downloads the pinned FFmpeg build into the user data folder (only on explicit request)."""
    return start_ffmpeg_download()


@app.post("/api/onnx-models/{model_id}/download")
def api_onnx_download(model_id: str) -> Dict[str, Any]:
    """GigaAM / Parakeet (app/asr_onnx.py): downloaded in the background."""
    from app import asr_onnx

    try:
        return asr_onnx.start_download(model_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/onnx-models/{model_id}")
def api_onnx_delete(model_id: str) -> Dict[str, Any]:
    from app import asr_onnx

    if not asr_onnx.is_onnx_model(model_id):
        raise HTTPException(status_code=404, detail="Неизвестная модель")
    return {"ok": asr_onnx.delete_model(model_id)}


@app.get("/api/models/status")
def api_models_status() -> Dict[str, Any]:
    from app.model_manager import get_models_status

    return get_models_status()


class ModelsSetupPayload(BaseModel):
    whisper_model: str = "large-v3-turbo"
    install_diarizer: bool = True


@app.post("/api/models/setup")
def api_models_setup(payload: ModelsSetupPayload) -> Dict[str, Any]:
    from app.model_manager import start_models_setup

    return start_models_setup(
        whisper_model_id=payload.whisper_model.strip(),
        install_diarizer=payload.install_diarizer,
    )


@app.delete("/api/models/{model_id}")
def api_delete_whisper_model(model_id: str) -> Dict[str, Any]:
    from app.model_manager import delete_whisper_model, get_models_status

    delete_whisper_model(model_id)
    return get_models_status()


class SettingsUpdatePayload(BaseModel):
    settings: Dict[str, Any]


@app.post("/api/settings")
def api_update_settings(payload: SettingsUpdatePayload) -> Dict[str, Any]:
    # Sending transcripts to a cloud service needs the consent given in Settings → Analysis
    if payload.settings.get("llm_provider") == "cloud" and get_all_settings().get("llm_cloud_consent") != "true" \
            and str(payload.settings.get("llm_cloud_consent")) != "true":
        raise HTTPException(status_code=400, detail="Подтвердите отправку текста в облачный сервис в «Настройки → Анализ»")
    updated = update_settings({k: v for k, v in payload.settings.items() if not str(k).startswith("llm_key_")})
    if "semantic_search" in payload.settings or "semantic_model" in payload.settings:
        from app import semantic

        semantic.kick()
    return {"settings": updated, "ollama": get_ollama_status()}


@app.get("/api/ollama/status")
def api_ollama_status() -> Dict[str, Any]:
    return get_ollama_status()


class OllamaPullPayload(BaseModel):
    model: str
    allow_install: bool = False


@app.post("/api/ollama/pull")
def api_ollama_pull(payload: OllamaPullPayload) -> Dict[str, Any]:
    return start_model_pull(payload.model.strip(), allow_install=payload.allow_install)


# --- Custom Dictionary ---
@app.get("/api/dictionary")
def api_list_dictionary() -> List[Dict[str, Any]]:
    return list_dictionary(active_only=False)


class DictionaryCreatePayload(BaseModel):
    term: str
    category: str = "Термин"


@app.post("/api/dictionary")
def api_add_dictionary(payload: DictionaryCreatePayload) -> Dict[str, Any]:
    try:
        # Support adding comma-separated or newline-separated batch of terms at once
        raw_terms = [
            t.strip()
            for line in payload.term.splitlines()
            for t in line.split(",")
            if t.strip()
        ]
        if not raw_terms:
            raise HTTPException(status_code=400, detail="Введите хотя бы одно слово или фразу")
        last_item = {}
        for t in raw_terms:
            last_item = add_dictionary_term(t, payload.category)
        return {"added": len(raw_terms), "last": last_item, "items": list_dictionary()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class DictionaryTogglePayload(BaseModel):
    active: bool


@app.patch("/api/dictionary/{term_id}")
def api_toggle_dictionary(term_id: int, payload: DictionaryTogglePayload) -> Dict[str, Any]:
    toggle_dictionary_term(term_id, payload.active)
    return {"ok": True}


@app.delete("/api/dictionary")
def api_clear_dictionary() -> Dict[str, Any]:
    from app.database import clear_dictionary

    clear_dictionary()
    return {"ok": True}


@app.delete("/api/dictionary/{term_id}")
def api_delete_dictionary(term_id: int) -> Dict[str, Any]:
    delete_dictionary_term(term_id)
    return {"ok": True}


# --- Live progress (Server-Sent Events) ---
_SSE_HEARTBEAT_SEC = 15.0


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    """
    Pushes processing progress and library changes to the UI instead of it polling every 2 s.
    Events: {"type": "progress", "id", status?, progress?, stage_text?, error_message?, duration?}
    and {"type": "list"} (a recording was added, renamed or deleted).
    """
    sub = events.subscribe()

    async def stream():
        try:
            yield 'retry: 3000\ndata: {"type": "hello"}\n\n'
            while True:
                batch = await sub.next_batch(_SSE_HEARTBEAT_SEC)
                if await request.is_disconnected():
                    break
                if not batch:
                    yield ": ping\n\n"
                    continue
                for ev in batch:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            events.unsubscribe(sub)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --- Meetings & Batch Processing ---
@app.get("/api/meetings")
def api_list_meetings() -> List[Dict[str, Any]]:
    return list_meetings()


@app.get("/api/meetings/search")
def api_search_meetings(q: str = "") -> List[Dict[str, Any]]:
    from app import semantic
    from app.database import search_all_meetings

    results = search_all_meetings(q)
    # A phrase is also looked up by meaning: its hits come after the keyword ones, marked
    if len(q.split()) >= 2 and semantic.enabled():
        by_id = {r["meeting_id"]: r for r in results}
        for hit in semantic.search(q, limit=8):
            r = by_id.get(hit["meeting_id"])
            if r is None:
                m = get_meeting(hit["meeting_id"])
                if not m:
                    continue
                r = by_id[hit["meeting_id"]] = {"meeting_id": m["id"], "title": m["title"], "duration": m.get("duration") or 0.0,
                                                "status": m.get("status") or "completed", "hits": []}
                results.append(r)
            if any(abs(h["start"] - hit["start"]) < 20 for h in r["hits"]):
                continue
            first = hit["text"].split("\n", 1)[0]
            r["hits"].append({"segment_id": None, "start": hit["start"], "speaker": hit["speaker"],
                              "text": first.split(": ", 1)[-1], "semantic": True})
    return results


@app.get("/api/semantic/status")
def api_semantic_status() -> Dict[str, Any]:
    from app import semantic

    return semantic.status()


@app.post("/api/semantic/index")
def api_semantic_index() -> Dict[str, Any]:
    """Builds the meaning index now (an explicit action: Ollama may be started for it)."""
    from app import semantic
    from app.ollama_client import ensure_ollama_running

    settings = get_all_settings()
    if not ensure_ollama_running((settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1")):
        raise HTTPException(status_code=503, detail="Ollama не запущена: установите её в «Настройки → Анализ»")
    semantic.start_indexer()
    return semantic.status()


@app.get("/api/meetings/{meeting_id}")
def api_get_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return {**m, "local_only_effective": is_local_only(m)}


@app.get("/api/meetings/{meeting_id}/llm-estimate")
def api_llm_estimate(meeting_id: int, model: str = "") -> Dict[str, Any]:
    """Where the analysis of a recording would go and how big the request is, shown before it runs."""
    from app import llm
    from app.ollama_client import build_transcript_lines

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    local_only = is_local_only(m)
    settings = get_all_settings()
    cloud = (model == "cloud" or (not model and llm.uses_cloud(settings))) and not local_only
    text = "\n".join(build_transcript_lines(m.get("segments") or [], m.get("speakers") or {}))
    out: Dict[str, Any] = {"cloud": cloud, "local_only": local_only, "chars": len(text),
                           "tokens": llm.estimate_tokens(text) + 600}  # + the instructions
    if cloud:
        p = llm.cloud_provider(settings)
        out["label"] = f"{llm.PROVIDERS[p]['name']} · {llm.cloud_model(p, settings)}"
    return out


class LocalOnlyPayload(BaseModel):
    local_only: bool


@app.patch("/api/meetings/{meeting_id}/local-only")
def api_meeting_local_only(meeting_id: int, payload: LocalOnlyPayload) -> Dict[str, Any]:
    """Marks a recording "local analysis only": it is never sent to a cloud model."""
    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    update_meeting_fields(meeting_id, local_only=1 if payload.local_only else 0)
    return api_get_meeting(meeting_id)


def _recordings_dir() -> Path:
    return recordings_dir(get_all_settings().get("recordings_dir") or "")


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


@app.get("/api/calls/status")
def api_calls_status() -> Dict[str, Any]:
    from app import call_watch

    return call_watch.status()


@app.get("/api/hotkey")
def api_hotkey() -> Dict[str, Any]:
    """Whether the recording hotkey is registered (it lives in the desktop window's tray thread)."""
    from app.hotkey import hotkey_state

    return dict(hotkey_state)


@app.get("/api/disk")
def api_disk() -> Dict[str, Any]:
    return disk_state()


def _local_time(ts: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _file_time(path: Path) -> str:
    """When a media file was made: the modification time survives copying, the creation time does not."""
    try:
        return _local_time(path.stat().st_mtime)
    except OSError:
        return ""


_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@app.post("/api/meetings/upload")
async def api_upload_meetings(
    files: List[UploadFile] = File(...),
    language: str = Form("auto"),
    model_name: str = Form(""),
    num_speakers: int = Form(0),
    source: str = Form("import"),  # "recording": made in the studio; "import": a file added by the user
    title: str = Form(""),
    folder_id: Optional[int] = Form(None),
    last_modified: List[str] = Form([]),  # File.lastModified of each file, ms since the epoch
) -> Dict[str, Any]:
    settings = get_all_settings()
    chosen_model = model_name.strip() or settings.get("whisper_model", "large-v3-turbo")
    chosen_lang = language.strip() or settings.get("language", "auto")
    chosen_speakers = num_speakers if num_speakers > 0 else int(settings.get("num_speakers", 0))
    is_recording = source == "recording"
    target_dir = _recordings_dir() if is_recording else UPLOADS_DIR

    created = []
    for i, uf in enumerate(files):
        orig_name = uf.filename or "recording.mp4"
        ext = Path(orig_name).suffix.lower() or ".mp4"
        safe_stem = _UNSAFE_NAME_RE.sub("_", Path(orig_name).stem)[:120] or "recording"
        dest_path = target_dir / f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"

        with dest_path.open("wb") as out_f:
            shutil.copyfileobj(uf.file, out_f)

        recorded_at = ""
        if not is_recording and i < len(last_modified):
            try:
                ms = float(last_modified[i])
                recorded_at = _local_time(ms / 1000.0) if ms > 0 else ""
            except (ValueError, OSError, OverflowError):
                recorded_at = ""
        m = create_meeting(
            title=(title.strip()[:300] if title.strip() and len(files) == 1 else safe_stem),
            filename=orig_name,
            media_path=str(dest_path),
            language=chosen_lang,
            model_name=chosen_model,
            num_speakers_hint=chosen_speakers,
            source="recording" if is_recording else "import",
            folder_id=folder_id,
            owns_media=True,
            recorded_at=recorded_at,
            title_is_default=True,
        )
        enqueue_meeting_job(m["id"])
        created.append(m)

    return {"created": created}


class LocalPathPayload(BaseModel):
    path: str
    language: str = "auto"
    model_name: str = ""
    num_speakers: int = 0


@app.post("/api/meetings/local-path")
def api_add_local_path(payload: LocalPathPayload) -> Dict[str, Any]:
    """
    Allows adding a local file or an entire folder of recordings directly from disk
    without duplicating large video files.
    """
    raw_path = payload.path.strip().strip('"').strip("'")
    target = Path(raw_path)
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"Путь не найден на диске: {raw_path}")

    settings = get_all_settings()
    chosen_model = payload.model_name.strip() or settings.get("whisper_model", "large-v3-turbo")
    chosen_lang = payload.language.strip() or settings.get("language", "auto")
    chosen_speakers = (
        payload.num_speakers if payload.num_speakers > 0 else int(settings.get("num_speakers", 0))
    )

    media_files: List[Path] = []
    if target.is_file():
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Формат файла {target.suffix} не поддерживается",
            )
        media_files.append(target)
    elif target.is_dir():
        for p in sorted(target.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                media_files.append(p)
        if not media_files:
            raise HTTPException(
                status_code=400,
                detail="В указанной папке не найдено поддерживаемых видео или аудио файлов",
            )

    created = []
    for mf in media_files:
        m = create_meeting(
            title=mf.stem,
            filename=mf.name,
            media_path=str(mf.resolve()),
            language=chosen_lang,
            model_name=chosen_model,
            num_speakers_hint=chosen_speakers,
            source="local",
            recorded_at=_file_time(mf),
            title_is_default=True,
        )
        enqueue_meeting_job(m["id"])
        created.append(m)

    return {"created": created}


class MeetingTitlePayload(BaseModel):
    title: str


@app.patch("/api/meetings/{meeting_id}/title")
def api_rename_meeting(meeting_id: int, payload: MeetingTitlePayload) -> Dict[str, Any]:
    from app.obsidian import refresh_if_exported

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    # A title the user gave is never replaced by one made from the content
    update_meeting_fields(meeting_id, title=title[:300], title_is_default=0)
    from app.naming import rename_recording_file

    rename_recording_file(meeting_id)
    refresh_if_exported(meeting_id)
    return get_meeting(meeting_id)


@app.post("/api/meetings/{meeting_id}/retry")
def api_retry_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    _ensure_not_processing(meeting_id)
    update_meeting_progress(
        meeting_id,
        status="queued",
        progress=0,
        stage_text="Повторная постановка в очередь...",
        error_message="",
    )
    enqueue_meeting_job(meeting_id)
    return get_meeting(meeting_id)


class MeetingReconfigurePayload(BaseModel):
    model_name: str
    language: str = "auto"
    num_speakers_hint: int = 0


@app.post("/api/meetings/{meeting_id}/reconfigure")
def api_reconfigure_meeting(meeting_id: int, payload: MeetingReconfigurePayload) -> Dict[str, Any]:
    from app.database import update_meeting_fields

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    _ensure_not_processing(meeting_id)

    update_meeting_fields(
        meeting_id,
        model_name=payload.model_name.strip() or "large-v3-turbo",
        language=payload.language.strip() or "auto",
        num_speakers_hint=max(0, int(payload.num_speakers_hint)),
    )
    update_meeting_progress(
        meeting_id,
        status="queued",
        progress=0,
        stage_text="Повторная обработка с новыми параметрами...",
        error_message="",
    )
    enqueue_meeting_job(meeting_id)
    return get_meeting(meeting_id)


@app.post("/api/meetings/{meeting_id}/cancel")
def api_cancel_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if not request_cancel(meeting_id):
        raise HTTPException(status_code=409, detail="Запись сейчас не обрабатывается")
    update_meeting_progress(meeting_id, stage_text="Отмена обработки...")
    return get_meeting(meeting_id)


@app.post("/api/meetings/{meeting_id}/reveal")
def api_reveal_meeting_file(meeting_id: int) -> Dict[str, Any]:
    import subprocess

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    media_path = Path(m.get("media_path", ""))
    audio_path = Path(m.get("audio_path", ""))
    target = media_path if media_path.exists() else audio_path
    if not target or not target.exists():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    try:
        subprocess.Popen(["explorer.exe", f"/select,{target.resolve()}"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@app.delete("/api/meetings/{meeting_id}")
def api_delete_meeting(meeting_id: int, with_source: bool = False) -> Dict[str, Any]:
    if is_job_active(meeting_id):
        request_cancel(meeting_id)
        raise HTTPException(status_code=409, detail="Запись обрабатывается: обработка отменяется, удалите её через несколько секунд.")
    ok = delete_meeting(meeting_id, with_source=with_source)
    if not ok:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return {"ok": True}


# --- Speaker Renaming & Segment Editing ---
class SpeakerUpdatePayload(BaseModel):
    speaker_id: str
    name: str
    color: Optional[str] = None


@app.patch("/api/meetings/{meeting_id}/speakers")
def api_update_speaker(meeting_id: int, payload: SpeakerUpdatePayload) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    if not _SPEAKER_ID_RE.match(payload.speaker_id):
        raise HTTPException(status_code=400, detail="Некорректный идентификатор участника")
    if payload.color and not _COLOR_RE.match(payload.color):
        raise HTTPException(status_code=400, detail="Цвет должен быть в формате #RRGGBB")
    name = payload.name.strip()[:120]

    speakers = m.get("speakers") or {}
    if payload.speaker_id not in speakers:
        speakers[payload.speaker_id] = {
            "id": payload.speaker_id,
            "name": name or payload.speaker_id,
            "color": payload.color or "#6366f1",
            "sample_start": 0.0,
            "sample_end": 5.0,
        }
    else:
        old_name = speakers[payload.speaker_id].get("name") or ""
        speakers[payload.speaker_id]["name"] = name or payload.speaker_id
        if payload.color:
            speakers[payload.speaker_id]["color"] = payload.color
        if name and name != old_name:
            # Naming a voice teaches its profile: later recordings get the name automatically
            from app.voice_profiles import learn

            try:
                learn(name, speakers[payload.speaker_id].get("embedding"))
            except Exception:
                traceback.print_exc()
            speakers[payload.speaker_id].pop("voice_profile", None)

    save_meeting_results(meeting_id, speakers=speakers)
    return get_meeting(meeting_id)


# --- System audio recorded by the app itself (app/system_audio.py): no screen-share prompt ---
class RecordStartPayload(BaseModel):
    system: bool = True


@app.post("/api/record/start")
def api_record_start(payload: RecordStartPayload) -> Dict[str, Any]:
    from app.system_audio import start_capture

    if not payload.system:
        return {"capture_id": None}
    if disk_state()["critical"]:
        raise HTTPException(status_code=507, detail="Недостаточно места на диске для записи")
    try:
        cap = start_capture(UPLOADS_DIR / f"system_{uuid.uuid4().hex[:8]}.wav")
    except Exception as e:  # the page falls back to the browser's screen-share capture
        raise HTTPException(status_code=503, detail=str(e)[:300])
    return {"capture_id": cap.id}


@app.get("/api/record/{capture_id}/level")
def api_record_level(capture_id: str) -> Dict[str, Any]:
    from app.system_audio import get_capture

    cap = get_capture(capture_id)
    if not cap:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"level": cap.level, "error": cap.error}


@app.post("/api/record/{capture_id}/cancel")
def api_record_cancel(capture_id: str) -> Dict[str, Any]:
    from app.system_audio import finish_capture

    cap = finish_capture(capture_id)
    if cap:
        cap.path.unlink(missing_ok=True)
    return {"ok": True}


# --- What the page records (microphone, and the screen or a window), streamed to the server ---
# The page sends its MediaRecorder output every second, so a long recording with video never has to
# fit into the window's memory, and what was recorded survives if the window is closed.
_page_recordings: Dict[str, Path] = {}
_page_lock = threading.Lock()


def _page_part(page_id: str) -> Path:
    with _page_lock:
        p = _page_recordings.get(page_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return p


@app.post("/api/record/page/start")
def api_record_page_start() -> Dict[str, Any]:
    if disk_state()["critical"]:
        raise HTTPException(status_code=507, detail="Недостаточно места на диске для записи")
    page_id = uuid.uuid4().hex
    part = _recordings_dir() / f".page_{page_id[:12]}.webm.part"
    part.write_bytes(b"")
    with _page_lock:
        _page_recordings[page_id] = part
    return {"page_id": page_id}


@app.post("/api/record/page/{page_id}/chunk")
async def api_record_page_chunk(page_id: str, request: Request) -> Dict[str, Any]:
    part = _page_part(page_id)
    data = await request.body()
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Слишком большой фрагмент записи")
    with part.open("ab") as f:
        f.write(data)
    return {"ok": True, "size": part.stat().st_size, "free_bytes": _free_bytes(part.parent)}


@app.post("/api/record/page/{page_id}/cancel")
def api_record_page_cancel(page_id: str) -> Dict[str, Any]:
    with _page_lock:
        part = _page_recordings.pop(page_id, None)
    if part:
        part.unlink(missing_ok=True)
    return {"ok": True}


# --- The screen or a window recorded by the app (app/screen_capture.py), when the window's own
# screen picker is not available ---
class ScreenStartPayload(BaseModel):
    target: str = "desktop"  # "desktop" or the exact title of a window


@app.get("/api/record/screen/windows")
def api_screen_windows() -> Dict[str, Any]:
    from app import screen_capture

    return {"supported": screen_capture.supported(), "windows": screen_capture.list_windows()}


@app.post("/api/record/screen/start")
def api_screen_start(payload: ScreenStartPayload) -> Dict[str, Any]:
    from app import screen_capture
    from app.ffmpeg_manager import find_tool

    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="Для записи экрана нужен FFmpeg (Настройки → Общие)")
    if disk_state()["critical"]:
        raise HTTPException(status_code=507, detail="Недостаточно места на диске для записи")
    path = _recordings_dir() / f".screen_{uuid.uuid4().hex[:12]}.mkv.part"
    try:
        cap = screen_capture.start_capture(ffmpeg, path, payload.target.strip()[:300] or "desktop")
    except RuntimeError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Не удалось начать запись экрана: {e}")
    return {"screen_id": cap.id}


@app.post("/api/record/screen/{screen_id}/cancel")
def api_screen_cancel(screen_id: str) -> Dict[str, Any]:
    from app import screen_capture

    cap = screen_capture.finish_capture(screen_id)
    if cap:
        cap.path.unlink(missing_ok=True)
    return {"ok": True}


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


class RecordFinishPayload(BaseModel):
    title: str = "Запись"
    page_id: Optional[str] = None     # what the page recorded (microphone and/or video)
    capture_id: Optional[str] = None  # system audio recorded by the app
    screen_id: Optional[str] = None   # the screen or a window recorded by the app
    has_video: bool = False           # the page recording has a video track (screen, window, tab)
    has_mic: bool = False             # the page recording has an audio track


def _run_ffmpeg(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


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


@app.post("/api/record/finish")
async def api_record_finish(payload: RecordFinishPayload) -> Dict[str, Any]:
    """Ends a recording: stops the system-audio capture, joins it with what the page recorded
    (microphone, video) and queues the result for transcription."""
    from starlette.concurrency import run_in_threadpool

    from app.ffmpeg_manager import tool_cmd
    from app.system_audio import finish_capture

    page: Optional[Path] = None
    if payload.page_id:
        with _page_lock:
            page = _page_recordings.pop(payload.page_id, None)
        if page is not None and (not page.is_file() or page.stat().st_size == 0):
            page.unlink(missing_ok=True)
            page = None
    cap = finish_capture(payload.capture_id) if payload.capture_id else None
    system = cap.path if cap and cap.path.is_file() else None
    screen: Optional[Path] = None
    if payload.screen_id:
        from app import screen_capture

        scap = await run_in_threadpool(screen_capture.finish_capture, payload.screen_id)
        if scap and scap.path.is_file() and scap.path.stat().st_size > 0:
            screen = scap.path
    if not page and not system and not screen:
        raise HTTPException(status_code=404, detail="Запись пуста: звук не был получен")

    safe_stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", payload.title.strip() or "Запись")[:100]
    if screen:
        audio = [p for p in (page if payload.has_mic else None, system) if p]
        dest = _recordings_dir() / f"{uuid.uuid4().hex[:8]}_{safe_stem}.mp4"
        res = await run_in_threadpool(_run_ffmpeg, screen_mix_command(tool_cmd("ffmpeg"), screen, audio, dest))
        if res.returncode != 0 or not dest.is_file():
            raise HTTPException(status_code=500, detail="Не удалось свести запись: " + (res.stderr or "")[-300:])
        for p in (screen, page, system):
            if p:
                p.unlink(missing_ok=True)
        return _queue_recording(payload, dest, safe_stem, has_video=True)

    has_video = bool(payload.has_video and page)
    ext = ".webm" if page and (has_video or not system) else ".m4a"
    dest = _recordings_dir() / f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    cmd = record_mix_command(tool_cmd("ffmpeg"), page, system, has_video, payload.has_mic, dest)
    if cmd is None:
        shutil.move(str(page), str(dest))  # the recordings folder may have changed to another drive
    else:
        res = await run_in_threadpool(_run_ffmpeg, cmd)
        if res.returncode != 0 or not dest.is_file():
            if page and not system:
                dest.unlink(missing_ok=True)
                shutil.move(str(page), str(dest))  # the original is still a playable recording
            else:
                raise HTTPException(status_code=500, detail="Не удалось свести запись: " + (res.stderr or "")[-300:])
        if page:
            page.unlink(missing_ok=True)
    if system:
        system.unlink(missing_ok=True)
    return _queue_recording(payload, dest, safe_stem, has_video=has_video)


def _queue_recording(payload: RecordFinishPayload, dest: Path, safe_stem: str, has_video: bool) -> Dict[str, Any]:
    settings = get_all_settings()
    m = create_meeting(
        title=payload.title.strip()[:300] or safe_stem,
        filename=dest.name,
        media_path=str(dest),
        language=settings.get("language", "auto"),
        model_name=settings.get("whisper_model", "large-v3-turbo"),
        num_speakers_hint=int(settings.get("num_speakers", 0)),
        source="recording",
        owns_media=True,
        title_is_default=True,
        has_video=has_video,
    )
    from app.naming import rename_recording_file

    rename_recording_file(m["id"])  # "2026-10-20 Запись 14-05.m4a"; renamed again once titled
    enqueue_meeting_job(m["id"])
    return {"created": [get_meeting(m["id"]) or m]}


class UiLangPayload(BaseModel):
    lang: str = "ru"


@app.post("/api/ui/lang")
def api_ui_lang(payload: UiLangPayload) -> Dict[str, Any]:
    """Remembers the UI language for the launcher's splash screen (read by VerbaScribe.exe)."""
    lang = "en" if payload.lang == "en" else "ru"
    try:
        (DATA_DIR / "ui-lang.txt").write_text(lang, encoding="utf-8")
    except OSError:
        pass
    return {"lang": lang}


# --- What the page is doing, for the tray icon (see app/app_status.py) ---
class UiStatePayload(BaseModel):
    recording: bool = False


@app.post("/api/ui/state")
def api_ui_state(payload: UiStatePayload) -> Dict[str, Any]:
    from app.app_status import set_recording, snapshot

    set_recording(payload.recording)
    st = snapshot()
    return {"state": st["state"], "text": st["text"]}


# --- Live captions while recording (see app/live.py) ---
class LiveStartPayload(BaseModel):
    lang: Optional[str] = None
    translate_to: Optional[str] = None
    capture_id: Optional[str] = None  # system audio recorded by the app is mixed into the captions
    speed: str = "quality"            # "quality": the configured model, "fast": a small installed one
    pair: Optional[List[str]] = None  # with translate_to="auto": the two languages, e.g. ["ru", "en"]
    draft: bool = False               # translate the phrase still being spoken too


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


@app.post("/api/live/start")
def api_live_start(payload: LiveStartPayload) -> Dict[str, Any]:
    from app.live import start_session

    target, pair = _live_target(payload.translate_to, payload.pair)
    s = start_session(payload.lang, target, payload.capture_id, payload.speed, pair)
    s.draft = payload.draft
    return s.state()


@app.post("/api/live/{session_id}/audio")
async def api_live_audio(session_id: str, request: Request, since: int = 0) -> Dict[str, Any]:
    """Body: 16 kHz mono 16-bit little-endian PCM. Returns new final lines, translations and the draft."""
    from starlette.concurrency import run_in_threadpool

    from app.live import get_session

    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Сеанс субтитров не найден")
    data = await request.body()
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Слишком большой фрагмент звука")
    s.add_pcm16(data)
    await run_in_threadpool(s.step)
    return s.state(since)


class LiveConfigPayload(BaseModel):
    translate_to: Optional[str] = None
    speed: Optional[str] = None
    speech_lang: Optional[str] = None  # "auto" or a language code; None keeps the current one
    pair: Optional[List[str]] = None
    draft: Optional[bool] = None
    caption_model: Optional[str] = None    # "auto" or a model id; None keeps the current one
    translate_model: Optional[str] = None  # "" for auto (by speed) or an Ollama model; None keeps it


class LiveAskPayload(BaseModel):
    question: str
    lang: str = "ru"


@app.post("/api/live/{session_id}/ask")
def api_live_ask(session_id: str, payload: LiveAskPayload) -> Dict[str, Any]:
    """A question about what was said so far in a running recording (its live captions)."""
    from app.audio_utils import format_timestamp
    from app.live import get_session
    from app.llm import LlmError, target
    from app.ollama_client import _lang_phrase

    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Субтитры этой записи не идут")
    question = payload.question.strip()[:500]
    if not question:
        raise HTTPException(status_code=400, detail="Пустой вопрос")
    lines = [f"[{format_timestamp(ln['start'])}] {ln['text']}" for ln in s.lines if (ln.get("text") or "").strip()]
    if s.partial:
        lines.append(f"[сейчас] {s.partial}")
    if not lines:
        raise HTTPException(status_code=400, detail="Пока ничего не сказано")
    try:
        llm = target(None)
    except LlmError as e:
        raise HTTPException(status_code=503, detail=str(e))
    text = "\n".join(lines)[-max(6000, llm.max_chars - 2000):]  # the latest part if the call is long
    system = ("Ты помогаешь во время идущего разговора. Отвечай ТОЛЬКО по расшифровке ниже (она из живых субтитров и может "
              "содержать ошибки распознавания). Если ответа нет — так и скажи. Коротко, по делу, 1–4 предложения, "
              f"ссылайся на время в квадратных скобках. Отвечай {_lang_phrase(payload.lang)}.")
    try:
        answer = llm.chat(system, f"Расшифровка разговора:\n{text}\n\nВопрос: {question}")
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)[:300])
    return {"answer": answer, "model": llm.label, "cloud": llm.is_cloud}


@app.post("/api/live/{session_id}/config")
def api_live_config(session_id: str, payload: LiveConfigPayload) -> Dict[str, Any]:
    """Changes the caption translation while recording (language, fast or quality model)."""
    from app.live import get_session

    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Сеанс субтитров не найден")
    target, pair = _live_target(payload.translate_to, payload.pair)
    if payload.draft is not None:
        s.draft = payload.draft
    if payload.caption_model is not None:
        s.set_caption_model(payload.caption_model)
    if payload.translate_model is not None:
        s.set_translate_model(payload.translate_model)
    s.configure(target, payload.speed if payload.speed in ("quality", "fast") else None, payload.speech_lang, pair)
    return s.state(0)


@app.get("/api/live/models")
def api_live_models() -> Dict[str, Any]:
    from app.live_translate import model_choices

    return model_choices()


@app.post("/api/live/{session_id}/stop")
def api_live_stop(session_id: str) -> Dict[str, Any]:
    from app.live import stop_session

    stop_session(session_id)
    return {"ok": True}


class LibraryAskPayload(BaseModel):
    question: str
    lang: str = "ru"
    model: Optional[str] = None
    folder_id: Optional[int] = None  # only recordings in this folder and the folders inside it
    meeting_ids: Optional[List[int]] = None  # only these recordings


@app.post("/api/library/ask")
def api_library_ask(payload: LibraryAskPayload) -> Dict[str, Any]:
    """Answers a question from the transcripts of all recordings, a folder or chosen ones (app/library_qa.py)."""
    from app.database import get_folder, meeting_ids_in_folder
    from app.library_qa import ask_library

    scope: Optional[List[int]] = None
    if payload.folder_id is not None:
        if not get_folder(payload.folder_id):
            raise HTTPException(status_code=404, detail="Папка не найдена")
        scope = meeting_ids_in_folder(payload.folder_id)
    if payload.meeting_ids is not None:
        ids = [int(i) for i in payload.meeting_ids][:500]
        scope = ids if scope is None else [i for i in scope if i in ids]
    try:
        return ask_library(payload.question, answer_lang=payload.lang, model_name=payload.model, meeting_ids=scope)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


# --- Series of recurring meetings (app/series.py) ---
@app.get("/api/series")
def api_list_series() -> List[Dict[str, Any]]:
    from app.series import list_series

    return list_series()


class SeriesAssignPayload(BaseModel):
    series_id: Optional[int] = None  # None: out of any series
    new_name: Optional[str] = None   # a new series with this name


@app.patch("/api/meetings/{meeting_id}/series")
def api_meeting_series(meeting_id: int, payload: SeriesAssignPayload) -> Dict[str, Any]:
    from app.series import create_series

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    sid = create_series(payload.new_name) if payload.new_name else payload.series_id
    update_meeting_fields(meeting_id, series_id=sid)
    return api_get_meeting(meeting_id)


class SeriesRenamePayload(BaseModel):
    name: str


@app.patch("/api/series/{series_id}")
def api_rename_series(series_id: int, payload: SeriesRenamePayload) -> Dict[str, Any]:
    from app.series import rename_series

    rename_series(series_id, payload.name)
    return {"ok": True}


class SinceLastPayload(BaseModel):
    lang: str = "ru"
    model: Optional[str] = None


@app.post("/api/meetings/{meeting_id}/since-last")
def api_since_last(meeting_id: int, payload: SinceLastPayload) -> Dict[str, Any]:
    """"Since last time": the previous meeting's tasks checked against this one."""
    from app.series import since_last_time

    try:
        md = since_last_time(meeting_id, payload.model or None, payload.lang or "ru")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)[:300])
    m = get_meeting(meeting_id) or {}
    summaries = {**(m.get("summaries") or {}), "since_last": md}
    save_meeting_results(meeting_id, summaries=summaries)
    return {"summaries": summaries}


class BriefPayload(BaseModel):
    series_id: Optional[int] = None
    person: Optional[str] = None
    lang: str = "ru"
    model: Optional[str] = None


@app.post("/api/library/brief")
def api_library_brief(payload: BriefPayload) -> Dict[str, Any]:
    """A brief before the next meeting of a series or with a person (app/series.py)."""
    from app.series import brief

    try:
        return brief(payload.series_id, payload.person, payload.model, payload.lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)[:300])


class DigestPayload(BaseModel):
    folder_id: Optional[int] = None  # a folder with its subfolders; None: the whole library
    days: Optional[int] = None       # only what was recorded in the last days
    lang: str = "ru"
    model: Optional[str] = None


@app.post("/api/library/digest")
def api_library_digest(payload: DigestPayload) -> Dict[str, Any]:
    """A digest of a folder or of a period (app/digest.py)."""
    from app.database import get_folder
    from app.digest import make_digest

    if payload.folder_id is not None and not get_folder(payload.folder_id):
        raise HTTPException(status_code=404, detail="Папка не найдена")
    try:
        return make_digest(payload.folder_id, payload.days, answer_lang=payload.lang, model_name=payload.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/people")
def api_people() -> List[Dict[str, Any]]:
    """Named speakers across the library and the recordings they speak in (app/voice_profiles.py)."""
    from app.voice_profiles import people

    return people()


@app.post("/api/voice-profiles/apply-all")
def api_voice_profiles_apply_all() -> Dict[str, Any]:
    from app.voice_profiles import apply_to_library

    result = apply_to_library()
    if result["meetings"]:
        from app import events

        events.publish({"type": "list"})
    return result


@app.get("/api/voice-profiles")
def api_voice_profiles() -> Dict[str, Any]:
    from app.voice_profiles import enabled, list_profiles

    return {"enabled": enabled(), "profiles": list_profiles()}


@app.delete("/api/voice-profiles/{profile_id}")
def api_delete_voice_profile(profile_id: int) -> Dict[str, Any]:
    from app.voice_profiles import delete_profile, enabled, list_profiles

    if not delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="Голосовой профиль не найден")
    return {"enabled": enabled(), "profiles": list_profiles()}


class SegmentUpdatePayload(BaseModel):
    text: Optional[str] = None
    speaker: Optional[str] = None


@app.patch("/api/meetings/{meeting_id}/segments/{segment_id}")
def api_update_segment(
    meeting_id: int, segment_id: int, payload: SegmentUpdatePayload
) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    if payload.speaker is not None and payload.speaker not in (m.get("speakers") or {}):
        raise HTTPException(status_code=400, detail="Неизвестный участник")

    segments = m.get("segments") or []
    found = False
    for seg in segments:
        if int(seg.get("id", -1)) == segment_id:
            if payload.text is not None:
                new_text = payload.text.strip()
                if not new_text:
                    raise HTTPException(status_code=400, detail="Текст реплики не может быть пустым")
                if new_text != (seg.get("text") or "").strip():
                    # The player renders words, so they must follow the edited text
                    seg["words"] = realign_words(
                        seg.get("words") or [],
                        new_text,
                        float(seg.get("start", 0.0)),
                        float(seg.get("end", 0.0)),
                    )
                    seg["text"] = new_text
            if payload.speaker is not None:
                seg["speaker"] = payload.speaker
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Реплика не найдена")

    save_meeting_results(meeting_id, segments=segments)
    return get_meeting(meeting_id)


@app.post("/api/meetings/{meeting_id}/segments/{segment_id}/merge")
def api_merge_segment(meeting_id: int, segment_id: int) -> Dict[str, Any]:
    from app.database import merge_segment_with_previous

    updated = merge_segment_with_previous(meeting_id, segment_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return updated


@app.post("/api/meetings/{meeting_id}/clean-fillers")
def api_clean_fillers(meeting_id: int) -> Dict[str, Any]:
    from app.transcriber import clean_meeting_transcript

    try:
        return clean_meeting_transcript(meeting_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/meetings/{meeting_id}/analytics")
def api_meeting_analytics(meeting_id: int) -> Dict[str, Any]:
    from app.analytics import conversation_analytics

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return conversation_analytics(m)


@app.get("/api/meetings/{meeting_id}/fillers")
def api_filler_stats(meeting_id: int) -> Dict[str, Any]:
    from app.database import filler_stats

    return filler_stats(meeting_id)


@app.post("/api/meetings/{meeting_id}/restore-segments")
def api_restore_segments(meeting_id: int) -> Dict[str, Any]:
    """Undoes the last filler cleanup (automatic or manual) for this recording."""
    from app.database import restore_segments_backup

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if not restore_segments_backup(meeting_id):
        raise HTTPException(status_code=400, detail="Нет сохранённой версии для восстановления")
    return get_meeting(meeting_id)


# --- AI Summary & Custom Q&A via Ollama ---
class SummaryRequestPayload(BaseModel):
    template: str = "protocol"
    custom_prompt: str = ""
    model: Optional[str] = None
    lang: Optional[str] = None


@app.post("/api/meetings/{meeting_id}/summary")
def api_generate_summary(meeting_id: int, payload: SummaryRequestPayload) -> Dict[str, Any]:
    from app.diarizer import unload_diarizer
    from app.hardware import clear_vram

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    segments = m.get("segments") or []
    speakers = m.get("speakers") or {}
    if not segments:
        raise HTTPException(status_code=400, detail="Нет текста записи для анализа")

    # Free any cached speech models from VRAM before invoking local LLM
    unload_diarizer()
    clear_vram()

    try:
        result_md = generate_meeting_summary(
            segments=segments,
            speakers=speakers,
            template_key=payload.template,
            custom_prompt=payload.custom_prompt,
            model_name=payload.model or None,
            answer_lang=payload.lang or m.get("language") or "ru",
            local_only=is_local_only(m),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    summaries = m.get("summaries") or {}
    key = "custom" if payload.custom_prompt.strip() else payload.template
    summaries[key] = result_md
    summaries.pop("error", None)
    save_meeting_results(meeting_id, summaries=summaries)
    if key != "custom":
        from app.obsidian import refresh_if_exported

        refresh_if_exported(meeting_id)
    return {"key": key, "summary": result_md, "summaries": summaries}


# --- Media Streaming for Interactive Player ---
@app.get("/api/meetings/{meeting_id}/media")
def api_stream_media(meeting_id: int) -> FileResponse:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    # An empty path is Path("."), the current folder: only real files count
    candidates = [Path(p) for p in (m.get("media_path"), m.get("audio_path")) if p]
    target = next((p for p in candidates if p.is_file()), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Медиафайл не найден")

    mime_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        path=str(target),
        media_type=mime_type or "application/octet-stream",
        filename=target.name,
    )


# --- Translate & Dub (prototype) ---
class DubRequestPayload(BaseModel):
    target_lang: str
    consent: bool = False


@app.get("/api/dub/status")
def api_dub_status() -> Dict[str, Any]:
    from app.dubbing import dub_environment_status

    return dub_environment_status()


@app.get("/api/queue")
def api_queue() -> List[Dict[str, Any]]:
    """Processing queue: the running job first, then waiting jobs in order."""
    from app.transcriber import queue_snapshot

    return queue_snapshot()


@app.post("/api/dub/setup")
def api_dub_setup() -> Dict[str, Any]:
    """Installs the optional dubbing environment (.venv-dub) after the user confirmed it in the UI."""
    from app.dub_setup import start_setup

    return start_setup()


@app.post("/api/dub/setup/cancel")
def api_dub_setup_cancel() -> Dict[str, Any]:
    from app.dub_setup import cancel_setup

    return cancel_setup()


def _dub_job_running() -> bool:
    from app.transcriber import running_job_kind

    return running_job_kind() == "dub"


@app.get("/api/dub/info")
def api_dub_info() -> Dict[str, Any]:
    """Everything the Settings section shows: environment, model weights, disk usage."""
    from app.dub_models import disk_usage, weights_status
    from app.dub_setup import DUB_READY_MARKER, DUB_VENV
    from app.dubbing import DUBS_DIR, _ffmpeg_has_filter, dub_environment_status

    env = dub_environment_status()
    verified = ""
    try:
        if DUB_READY_MARKER.is_file():
            verified = DUB_READY_MARKER.read_text(encoding="utf-8").strip()[:200]
    except OSError:
        pass
    return {
        **env,
        "venv": str(DUB_VENV),
        "venv_exists": DUB_VENV.is_dir(),
        "verified": verified,
        "weights": weights_status(),
        "disk": disk_usage(DUB_VENV, DUBS_DIR),
        "rubberband": _ffmpeg_has_filter("rubberband"),
        "dub_running": _dub_job_running(),
    }


@app.post("/api/dub/verify")
def api_dub_verify() -> Dict[str, Any]:
    """Re-runs the import check of the dub environment (takes 10-30 s)."""
    from app import dub_setup

    if dub_setup.setup_status()["active"]:
        raise HTTPException(status_code=409, detail="Идёт установка окружения")
    ok = dub_setup._verify()
    if not ok:
        dub_setup.DUB_READY_MARKER.unlink(missing_ok=True)
    return {"ok": ok, "setup": dub_setup.setup_status()}


class DubWeightsPayload(BaseModel):
    models: Optional[List[str]] = None


@app.post("/api/dub/weights/download")
def api_dub_weights_download(payload: DubWeightsPayload) -> Dict[str, Any]:
    from app.dub_models import start_download

    return start_download(payload.models, _dub_job_running())


@app.post("/api/dub/weights/cancel")
def api_dub_weights_cancel() -> Dict[str, Any]:
    from app.dub_models import cancel_download

    return cancel_download()


@app.delete("/api/dub/weights/{model_id}")
def api_dub_weights_delete(model_id: str) -> Dict[str, Any]:
    from app.dub_models import delete_weights

    try:
        return delete_weights(model_id, _dub_job_running())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Models in VRAM (chips next to the GPU badge) ---
@app.get("/api/vram")
def api_vram() -> Dict[str, Any]:
    from app.vram import vram_snapshot

    return vram_snapshot()


class VramActionPayload(BaseModel):
    kind: str
    name: str = ""


@app.post("/api/vram/unload")
def api_vram_unload(payload: VramActionPayload) -> Dict[str, Any]:
    from app.vram import unload

    try:
        return unload(payload.kind, payload.name)
    except (RuntimeError, ValueError, httpx.HTTPError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/vram/preload")
def api_vram_preload(payload: VramActionPayload) -> Dict[str, Any]:
    from app.vram import preload

    try:
        return preload(payload.kind, payload.name)
    except (RuntimeError, ValueError, httpx.HTTPError) as e:
        raise HTTPException(status_code=409, detail=str(e))


# --- Updates from GitHub Releases (app/updater.py) ---
@app.get("/api/update")
def api_update_status(check: bool = False) -> Dict[str, Any]:
    from app import updater

    return updater.check(force=False) if check else updater.status()


@app.post("/api/update/check")
def api_update_check() -> Dict[str, Any]:
    from app import updater

    return updater.check(force=True)


@app.post("/api/update/download")
def api_update_download() -> Dict[str, Any]:
    from app import updater

    try:
        return updater.start_download()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/update/install")
def api_update_install() -> Dict[str, Any]:
    from app import updater
    from app.transcriber import running_job_kind

    if running_job_kind():
        raise HTTPException(status_code=409, detail="Дождитесь окончания текущей задачи")
    try:
        updater.launch_installer()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    updater.quit_soon()
    return {"ok": True}


@app.post("/api/meetings/{meeting_id}/dub")
def api_start_dub(meeting_id: int, payload: DubRequestPayload) -> Dict[str, Any]:
    from app.dubbing import DUB_LANGUAGES, dub_environment_status

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if not payload.consent:
        raise HTTPException(
            status_code=400,
            detail="Подтвердите, что у вас есть согласие участников на клонирование их голосов.",
        )
    if payload.target_lang not in DUB_LANGUAGES:
        raise HTTPException(status_code=400, detail="Этот язык не поддерживается для дубляжа")
    if m.get("status") != "completed" or not m.get("segments"):
        raise HTTPException(status_code=409, detail="Дубляж доступен только для полностью обработанной записи")
    if payload.target_lang == (m.get("language") or ""):
        raise HTTPException(status_code=400, detail="Язык перевода совпадает с языком записи")
    if not dub_environment_status()["installed"]:
        raise HTTPException(status_code=409, detail="Окружение дубляжа не установлено. Установите его в окне «Дубляж».")
    if not enqueue_dub_job(meeting_id, payload.target_lang):
        raise HTTPException(status_code=409, detail="Запись уже обрабатывается")
    return get_meeting(meeting_id)


def _find_dub(meeting_id: int, lang: str) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    for d in m.get("dubs") or []:
        if d.get("lang") == lang and Path(d.get("path") or "").exists():
            return d
    raise HTTPException(status_code=404, detail="Файл дубляжа не найден")


@app.get("/api/meetings/{meeting_id}/dub/{lang}/file")
def api_dub_file(meeting_id: int, lang: str) -> FileResponse:
    d = _find_dub(meeting_id, lang)
    target = Path(d["path"])
    mime_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(path=str(target), media_type=mime_type or "application/octet-stream", filename=target.name)


@app.post("/api/meetings/{meeting_id}/dub/{lang}/save/{kind}")
def api_dub_save(meeting_id: int, lang: str, kind: str) -> Dict[str, Any]:
    """Copies the dubbed file (kind=media) or its translated subtitles (kind=srt) to a folder the user picks."""
    d = _find_dub(meeting_id, lang)
    if kind == "media":
        src = Path(d["path"])
    elif kind == "srt":
        src = Path(d.get("srt_path") or Path(d["path"]).with_suffix(".srt"))
    else:
        raise HTTPException(status_code=400, detail="Неизвестный тип файла")
    if not src.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    fmt = src.suffix.lower().lstrip(".")
    chosen = _pick_path_native("save", _default_export_dir(), src.name, fmt)
    if not chosen:
        return {"ok": False, "cancelled": True}
    out_path = Path(chosen)
    if out_path.suffix.lower() != src.suffix.lower():
        out_path = out_path.with_name(out_path.name + src.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out_path)
    update_settings({"export_dir": str(out_path.parent)})
    return {"ok": True, "path": str(out_path), "filename": out_path.name, "folder": str(out_path.parent)}


@app.post("/api/meetings/{meeting_id}/dub/{lang}/reveal")
def api_dub_reveal(meeting_id: int, lang: str) -> Dict[str, Any]:
    import subprocess

    d = _find_dub(meeting_id, lang)
    subprocess.Popen(["explorer.exe", f"/select,{Path(d['path']).resolve()}"])
    return {"ok": True}


# --- Translate text ---
class TranslateRequestPayload(BaseModel):
    target_lang: str
    model: Optional[str] = None
    only_changed: bool = False  # translate only stale/missing segments


@app.get("/api/translate/languages")
def api_translate_languages() -> Dict[str, Any]:
    from app.translation import TEXT_LANGUAGES

    return {"languages": list(TEXT_LANGUAGES.keys())}


@app.post("/api/meetings/{meeting_id}/translate")
def api_start_translation(meeting_id: int, payload: TranslateRequestPayload) -> Dict[str, Any]:
    from app.translation import TEXT_LANGUAGES

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if payload.target_lang not in TEXT_LANGUAGES:
        raise HTTPException(status_code=400, detail="Этот язык не поддерживается")
    if m.get("status") != "completed" or not m.get("segments"):
        raise HTTPException(status_code=409, detail="Перевод доступен только для полностью обработанной записи")
    if payload.target_lang == (m.get("language") or ""):
        raise HTTPException(status_code=400, detail="Язык перевода совпадает с языком записи")
    model = (payload.model or "").strip() or None
    if not enqueue_translate_job(meeting_id, payload.target_lang, model, payload.only_changed):
        raise HTTPException(status_code=409, detail="Запись уже обрабатывается")
    return get_meeting(meeting_id)


@app.delete("/api/meetings/{meeting_id}/translations/{lang}")
def api_delete_translation(meeting_id: int, lang: str) -> Dict[str, Any]:
    from app.database import save_meeting_translation

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    _ensure_not_processing(meeting_id)
    save_meeting_translation(meeting_id, lang, None)
    return get_meeting(meeting_id)


class SubtitlesRequestPayload(BaseModel):
    lang: Optional[str] = None  # None/"" = subtitles in the original language
    variant: str = "translated"  # "translated" | "bilingual"
    mode: str = "soft"  # "soft" (copy streams + subtitle track) | "burn" (re-encode, queue job)
    ask_path: bool = False  # True: native Save dialog, otherwise DATA_DIR/exports


class CutPayload(BaseModel):
    kind: str = "clean"  # "clean": no pauses and fillers; "highlights": the key moments
    lang: str = "ru"
    model: Optional[str] = None


@app.post("/api/meetings/{meeting_id}/cut")
def api_meeting_cut(meeting_id: int, payload: CutPayload) -> Dict[str, Any]:
    """A shorter copy of the recording (app/media_cut.py), saved where the user picks."""
    from app.media_cut import kept_seconds, merge, pick_highlights, speech_intervals
    from app.naming import base_name
    from app.transcriber import enqueue_cut_job
    from app.video_subtitles import output_extension, probe_media

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if payload.kind not in ("clean", "highlights"):
        raise HTTPException(status_code=400, detail="Неизвестный вид нарезки")
    _ensure_not_processing(meeting_id)
    media = Path(m.get("media_path") or "")
    if not media.is_file():
        raise HTTPException(status_code=404, detail="Исходный файл не найден")
    duration = float(m.get("duration") or 0)
    moments: List[Dict[str, Any]] = []
    if payload.kind == "clean":
        intervals = speech_intervals(m.get("segments") or [], duration)
    else:
        try:
            moments = pick_highlights(m, payload.model, payload.lang)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        intervals = merge([(x["start"], x["end"]) for x in moments])
    if not intervals:
        raise HTTPException(status_code=400, detail="В записи не найдено речи")
    has_video = bool(probe_media(media)["has_video"])
    ext = output_extension(media) if has_video else ".m4a"
    suffix = ".highlights" if payload.kind == "highlights" else ".clean"
    chosen = _pick_path_native("save", _default_export_dir(), f"{base_name(m)}{suffix}{ext}", ext.lstrip("."))
    if not chosen:
        return {"ok": False, "cancelled": True}
    out = Path(chosen)
    if out.suffix.lower() != ext:
        out = out.with_name(out.name + ext)
    if out.resolve() == media.resolve():
        raise HTTPException(status_code=400, detail="Нельзя сохранить результат поверх исходного файла")
    update_settings({"export_dir": str(out.parent)})
    if not enqueue_cut_job(meeting_id, payload.kind, intervals, has_video, str(out)):
        raise HTTPException(status_code=409, detail="Запись сейчас обрабатывается")
    return {"ok": True, "queued": True, "filename": out.name, "path": str(out), "kept_seconds": round(kept_seconds(intervals), 1),
            "duration": duration, "moments": moments}


@app.post("/api/meetings/{meeting_id}/subtitles")
def api_video_subtitles(meeting_id: int, payload: SubtitlesRequestPayload) -> Dict[str, Any]:
    """Makes a copy of the recording's video with subtitles; the original file is never touched."""
    from app.translation import build_cues, cues_to_srt
    from app.video_subtitles import (
        check_output_path,
        default_output_path,
        mux_soft_subtitles,
        output_extension,
        probe_media,
    )

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if payload.mode not in ("soft", "burn"):
        raise HTTPException(status_code=400, detail="Неизвестный режим субтитров")
    _ensure_not_processing(meeting_id)
    lang = (payload.lang or "").strip()
    variant = payload.variant if lang else "original"
    if lang and variant not in _EXPORT_VARIANTS:
        raise HTTPException(status_code=400, detail="Неизвестный вариант субтитров")
    resolved = (m.get("translations") or {}).get(lang) if lang else None
    if lang and not resolved:
        raise HTTPException(status_code=404, detail=f"Перевода на язык {lang!r} нет")
    media = Path(m.get("media_path") or "")
    if not media.is_file():
        raise HTTPException(status_code=404, detail="Исходный видеофайл не найден")
    if not probe_media(media)["has_video"]:
        raise HTTPException(status_code=400, detail="В исходном файле нет видеодорожки")

    cues = build_cues(m.get("segments") or [], resolved, variant=variant, speakers=m.get("speakers"))
    if not cues:
        raise HTTPException(status_code=400, detail="Нет текста для субтитров")
    srt_text = cues_to_srt(cues)
    track_lang = lang or (m.get("language") or "")
    from app.naming import base_name

    out_path = default_output_path(base_name(m) or media.stem, media, track_lang or "orig", variant, payload.mode)
    if payload.ask_path:
        ext = output_extension(media)
        chosen = _pick_path_native("save", _default_export_dir(), out_path.name, ext.lstrip("."))
        if not chosen:
            return {"ok": False, "cancelled": True}
        out_path = Path(chosen)
        if out_path.suffix.lower() != ext:
            out_path = out_path.with_name(out_path.name + ext)
        update_settings({"export_dir": str(out_path.parent)})
    try:
        check_output_path(media, out_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    title = (
        f"{track_lang.upper()} + {(m.get('language') or '').upper()} (AI translation)" if variant == "bilingual"
        else f"{track_lang.upper()} (AI translation)" if lang else (track_lang.upper() or "Subtitles")
    )
    if payload.mode == "soft":
        try:
            mux_soft_subtitles(media, srt_text, track_lang, title, out_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:600])
        return {"ok": True, "queued": False, "path": str(out_path), "filename": out_path.name, "folder": str(out_path.parent)}

    if not enqueue_burn_subtitles_job(meeting_id, track_lang or "orig", srt_text, str(out_path)):
        raise HTTPException(status_code=409, detail="Запись уже обрабатывается")
    return {"ok": True, "queued": True, "path": str(out_path), "filename": out_path.name, "folder": str(out_path.parent)}


@app.get("/api/meetings/{meeting_id}/peaks")
def api_media_peaks(meeting_id: int, bars: int = 120) -> Dict[str, Any]:
    """Real loudness envelope of the extracted 16 kHz WAV for the player waveform."""
    import numpy as np
    import soundfile as sf

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    wav = Path(m.get("audio_path") or "")
    if not wav.is_file():
        return {"peaks": []}
    bars = max(20, min(400, int(bars)))
    try:
        data, _sr = sf.read(str(wav), dtype="float32", always_2d=False)
    except Exception:
        return {"peaks": []}
    if data.ndim > 1:
        data = data.mean(axis=1)
    if len(data) < bars:
        return {"peaks": []}
    usable = len(data) - (len(data) % bars)
    rms = np.sqrt(np.mean(np.square(data[:usable].reshape(bars, -1)), axis=1))
    top = float(np.percentile(rms, 98)) or 1.0
    peaks = np.clip(rms / top, 0.0, 1.0)
    return {"peaks": [round(float(v), 3) for v in peaks]}


# --- Multi-format Export ---
_EXPORT_VARIANTS = EXPORT_VARIANTS


def _build_export_bytes(
    m: Dict[str, Any], fmt: str, lang: Optional[str] = None, variant: str = "translated"
) -> tuple[bytes, str, str]:
    try:
        return build_export_bytes(m, fmt, lang, variant)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/meetings/{meeting_id}/export/{fmt}")
def api_export_meeting(meeting_id: int, fmt: str, lang: Optional[str] = None, variant: str = "translated") -> Response:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    content, media_type, filename = _build_export_bytes(m, fmt, lang, variant)
    encoded_filename = quote(filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
    }
    return Response(content=content, media_type=media_type, headers=headers)


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


def _pick_path_native(mode: str, directory: Path, filename: str = "", fmt: str = "") -> Optional[str]:
    """
    Spawns an isolated native file/folder dialog via app.native_dialog in a subprocess.
    This avoids thread-affinity problems and deadlocks with pywebview or uvicorn.
    """
    import subprocess

    desc, pat, ext = _EXPORT_FILE_TYPES.get(fmt.lower(), ("All files (*.*)", "*.*", ""))
    filter_arg = f"{desc}|{pat}|Все файлы (*.*)|*.*" if ext else "Все файлы (*.*)|*.*"

    cmd = [
        sys.executable,
        "-m",
        "app.native_dialog",
        "--mode",
        mode,
        "--dir",
        str(directory),
        "--filename",
        filename,
        "--filter",
        filter_arg,
        "--ext",
        ext,
        "--title",
        "Сохранить как" if mode == "save" else "Выбор папки для экспорта",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=300)
        out = (proc.stdout or "").strip()
        return out if out else None
    except Exception as e:
        print(f"[export] Native dialog failed: {e}")
        return None


@app.post("/api/meetings/{meeting_id}/save-export/{fmt}")
def api_save_export_to_disk(
    meeting_id: int, fmt: str, lang: Optional[str] = None, variant: str = "translated"
) -> Dict[str, Any]:
    """
    Asks the user where to save the export via a native Save dialog,
    writes the file there and remembers the chosen folder for next time.
    """
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    content, _, filename = _build_export_bytes(m, fmt, lang, variant)
    chosen = _pick_path_native("save", _default_export_dir(), filename, fmt.lower())
    if not chosen:
        return {"ok": False, "cancelled": True}

    out_path = Path(chosen)
    ext = _EXPORT_FILE_TYPES.get(fmt.lower(), ("", "", ""))[2]
    if ext and out_path.suffix.lower() != ext.lower():
        out_path = out_path.with_name(out_path.name + ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)
    update_settings({"export_dir": str(out_path.parent)})

    return {"ok": True, "path": str(out_path), "filename": out_path.name, "folder": str(out_path.parent)}


@app.post("/api/export-dir/pick")
def api_pick_export_dir() -> Dict[str, Any]:
    """Lets the user choose the default export folder via a native folder dialog."""
    chosen = _pick_path_native("folder", _default_export_dir())
    if not chosen:
        return {"ok": False, "cancelled": True}
    update_settings({"export_dir": chosen})
    return {"ok": True, "folder": chosen}


@app.get("/api/export-dir")
def api_get_export_dir() -> Dict[str, Any]:
    return {"folder": str(_default_export_dir())}


class RevealPayload(BaseModel):
    path: str


@app.post("/api/reveal")
def api_reveal_path(payload: RevealPayload) -> Dict[str, Any]:
    """Highlights a saved file (or opens a folder) in Windows Explorer."""
    import subprocess

    p = Path(payload.path)
    if not p.exists():
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
@app.get("/api/folders")
def api_list_folders() -> List[Dict[str, Any]]:
    from app.database import list_folders

    return list_folders()


class FolderCreatePayload(BaseModel):
    name: str
    parent_id: Optional[int] = None


@app.post("/api/folders")
def api_create_folder(payload: FolderCreatePayload) -> Dict[str, Any]:
    from app.database import create_folder

    try:
        return create_folder(payload.name, payload.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/api/folders/{folder_id}")
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


@app.delete("/api/folders/{folder_id}")
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


@app.patch("/api/meetings/{meeting_id}/folder")
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


@app.get("/api/meetings/{meeting_id}/thumb")
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


@app.get("/api/meetings/{meeting_id}/info")
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
@app.get("/api/recordings-dir")
def api_get_recordings_dir() -> Dict[str, Any]:
    return {"folder": str(_recordings_dir()), "default": str(paths.DEFAULT_RECORDINGS_DIR)}


@app.post("/api/recordings-dir/pick")
def api_pick_recordings_dir() -> Dict[str, Any]:
    chosen = _pick_path_native("folder", _recordings_dir())
    if not chosen:
        return {"ok": False, "cancelled": True}
    try:
        Path(chosen).mkdir(parents=True, exist_ok=True)
        probe = Path(chosen) / f".verbascribe-write-test-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"В эту папку нельзя записывать: {e}")
    update_settings({"recordings_dir": chosen})
    return {"ok": True, "folder": str(_recordings_dir())}


@app.get("/api/watch-folder")
def api_watch_folder_status() -> Dict[str, Any]:
    from app import watch_folder

    return watch_folder.status()


@app.post("/api/watch-folder/pick")
def api_watch_folder_pick() -> Dict[str, Any]:
    """Auto-import (app/watch_folder.py): files that appear in this folder are added by themselves."""
    current = get_all_settings().get("watch_folder") or ""
    chosen = _pick_path_native("folder", Path(current) if current and Path(current).is_dir() else Path.home())
    if not chosen:
        return {"ok": False, "cancelled": True}
    update_settings({"watch_folder": chosen})
    from app import watch_folder

    return {"ok": True, **watch_folder.status()}


@app.post("/api/watch-folder/off")
def api_watch_folder_off() -> Dict[str, Any]:
    update_settings({"watch_folder": "", "watch_folder_baseline": ""})
    from app import watch_folder

    return {"ok": True, **watch_folder.status()}


@app.post("/api/recordings-dir/reset")
def api_reset_recordings_dir() -> Dict[str, Any]:
    update_settings({"recordings_dir": ""})
    return {"ok": True, "folder": str(_recordings_dir())}


# --- Obsidian ---
@app.post("/api/obsidian/pick-vault")
def api_pick_obsidian_vault() -> Dict[str, Any]:
    current = (get_all_settings().get("obsidian_vault") or "").strip()
    chosen = _pick_path_native("folder", Path(current) if current and Path(current).is_dir() else Path.home())
    if not chosen:
        return {"ok": False, "cancelled": True}
    update_settings({"obsidian_vault": chosen})
    return {"ok": True, "folder": chosen, "is_vault": (Path(chosen) / ".obsidian").is_dir()}


@app.get("/api/obsidian/status")
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


@app.post("/api/meetings/{meeting_id}/obsidian")
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
    model: Optional[str] = None


@app.post("/api/meetings/{meeting_id}/export-request")
def api_export_request(meeting_id: int, payload: ExportRequestPayload) -> Dict[str, Any]:
    from app.pending_exports import request

    try:
        return request(meeting_id, payload.model_dump(exclude={"model"}), model=(payload.model or "").strip() or None)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/api/meetings/{meeting_id}/pending-exports")
def api_cancel_pending_exports(meeting_id: int) -> Dict[str, Any]:
    from app.pending_exports import cancel_all

    cancel_all(meeting_id)
    return {"ok": True}


@app.post("/api/vram/unload-all")
def api_vram_unload_all() -> Dict[str, Any]:
    """Frees every model the app holds now (the same as the idle timeout)."""
    from app import idle_unload
    from app.vram import vram_snapshot

    if idle_unload._busy():
        raise HTTPException(status_code=409, detail="Сейчас идёт обработка или запись")
    res = idle_unload.unload_now()
    return {**vram_snapshot(), **res}


# --- Cloud language models for analysis (app/llm.py) ---
@app.get("/api/llm/status")
def api_llm_status() -> Dict[str, Any]:
    from app.llm import status

    return status()


class LlmKeyPayload(BaseModel):
    provider: str
    key: str


@app.post("/api/llm/key")
def api_llm_set_key(payload: LlmKeyPayload) -> Dict[str, Any]:
    """Stores an API key (encrypted with DPAPI on Windows); it is never sent back to the page.
    The models the key can use are fetched at once; an unavailable chosen model is replaced."""
    from app import secret_store
    from app.llm import PROVIDERS, ensure_model, key_name, status

    if payload.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    key = payload.key.strip()
    if not key or len(key) > 500 or any(c.isspace() for c in key):
        raise HTTPException(status_code=400, detail="Ключ выглядит неверно")
    secret_store.set_secret(key_name(payload.provider), key)
    picked = ensure_model(payload.provider)
    return {**status(), "models": picked["models"], "model_changed": picked["changed"], "model": picked["model"]}


@app.delete("/api/llm/key/{provider}")
def api_llm_delete_key(provider: str) -> Dict[str, Any]:
    from app import secret_store
    from app.llm import PROVIDERS, key_name, status

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    secret_store.delete_secret(key_name(provider))
    return status()


@app.get("/api/llm/models/{provider}")
def api_llm_models(provider: str) -> Dict[str, Any]:
    from app.llm import PROVIDERS, list_models, pick_default_model

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    models = list_models(provider)
    return {"models": models, "recommended": pick_default_model(provider, models)}


class OpenUrlPayload(BaseModel):
    url: str


@app.post("/api/open-url")
def api_open_url(payload: OpenUrlPayload) -> Dict[str, Any]:
    """Opens a web link in the default browser (the app window itself never navigates away)."""
    import webbrowser
    from urllib.parse import urlparse

    u = urlparse(payload.url.strip())
    if u.scheme not in ("https", "http") or not u.netloc:
        raise HTTPException(status_code=400, detail="Можно открыть только веб-ссылку")
    webbrowser.open(u.geturl())
    return {"ok": True}


@app.post("/api/llm/test/{provider}")
def api_llm_test(provider: str) -> Dict[str, Any]:
    """Sends a two-word request to check the key, the address and the model."""
    from app.llm import PROVIDERS, LlmError, test_connection

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="Неизвестный сервис")
    try:
        return test_connection(provider)
    except LlmError as e:
        raise HTTPException(status_code=502, detail=str(e))
