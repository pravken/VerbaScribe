"""Recording: system audio, the page, the screen; finishing a recording; live captions."""

import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, Request
from pydantic import BaseModel
from app.paths import DATA_DIR, UPLOADS_DIR
from app.database import create_meeting, get_all_settings, get_meeting
from app.transcriber import enqueue_meeting_job

from fastapi import APIRouter

from app.main import (  # noqa: E402 - shared helpers live in app/main.py
    _free_bytes, _live_target, _recording_folder, _recordings_dir, _salvage_parts, disk_state, record_mix_command, screen_mix_command,
)

router = APIRouter()

# --- System audio recorded by the app itself (app/system_audio.py): no screen-share prompt ---
class RecordStartPayload(BaseModel):
    system: bool = True

@router.post("/api/record/start")
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

@router.get("/api/record/{capture_id}/level")
def api_record_level(capture_id: str) -> Dict[str, Any]:
    from app.system_audio import get_capture

    cap = get_capture(capture_id)
    if not cap:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"level": cap.level, "error": cap.error}

@router.post("/api/record/{capture_id}/cancel")
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

@router.post("/api/record/page/start")
def api_record_page_start() -> Dict[str, Any]:
    if disk_state()["critical"]:
        raise HTTPException(status_code=507, detail="Недостаточно места на диске для записи")
    page_id = uuid.uuid4().hex
    part = _recordings_dir() / f".page_{page_id[:12]}.webm.part"
    part.write_bytes(b"")
    with _page_lock:
        _page_recordings[page_id] = part
    return {"page_id": page_id}

@router.post("/api/record/page/{page_id}/chunk")
async def api_record_page_chunk(page_id: str, request: Request) -> Dict[str, Any]:
    part = _page_part(page_id)
    data = await request.body()
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Слишком большой фрагмент записи")
    with part.open("ab") as f:
        f.write(data)
    return {"ok": True, "size": part.stat().st_size, "free_bytes": _free_bytes(part.parent)}

@router.post("/api/record/page/{page_id}/cancel")
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

@router.get("/api/record/screen/windows")
def api_screen_windows() -> Dict[str, Any]:
    from app import screen_capture

    return {"supported": screen_capture.supported(), "windows": screen_capture.list_windows()}

@router.post("/api/record/screen/start")
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

@router.post("/api/record/screen/{screen_id}/cancel")
def api_screen_cancel(screen_id: str) -> Dict[str, Any]:
    from app import screen_capture

    cap = screen_capture.finish_capture(screen_id)
    if cap:
        cap.path.unlink(missing_ok=True)
    return {"ok": True}

class RecordFinishPayload(BaseModel):
    title: str = "Запись"
    page_id: Optional[str] = None     # what the page recorded (microphone and/or video)
    capture_id: Optional[str] = None  # system audio recorded by the app
    screen_id: Optional[str] = None   # the screen or a window recorded by the app
    has_video: bool = False           # the page recording has a video track (screen, window, tab)
    has_mic: bool = False             # the page recording has an audio track
    notes: str = ""                   # what the user wrote in the studio during the recording
    started_at: float = 0             # when the recording began (unix seconds): the calendar meeting

def _run_ffmpeg(cmd: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:  # FFmpeg missing: reported like a failed run, so the parts are kept
        return subprocess.CompletedProcess(cmd, 1, "", str(e))

@router.post("/api/record/finish")
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
        dest = _recording_folder() / f"{uuid.uuid4().hex[:8]}_{safe_stem}.mp4"
        res = await run_in_threadpool(_run_ffmpeg, screen_mix_command(tool_cmd("ffmpeg"), screen, audio, dest))
        if res.returncode != 0 or not dest.is_file():
            print("[record] mix failed, parts kept:", (res.stderr or "")[-300:], flush=True)
            dest.unlink(missing_ok=True)
            return _salvage_parts(payload.title.strip() or "Запись",
                                  [(screen, True), *[(p, p is page and payload.has_video) for p in (page, system) if p]],
                                  payload.notes)
        for p in (screen, page, system):
            if p:
                p.unlink(missing_ok=True)
        return _queue_recording(payload, dest, safe_stem, has_video=True)

    has_video = bool(payload.has_video and page)
    ext = ".webm" if page and (has_video or not system) else ".m4a"
    dest = _recording_folder() / f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    cmd = record_mix_command(tool_cmd("ffmpeg"), page, system, has_video, payload.has_mic, dest)
    if cmd is None:
        await run_in_threadpool(shutil.move, str(page), str(dest))  # the recordings folder may be on another drive
    else:
        res = await run_in_threadpool(_run_ffmpeg, cmd)
        if res.returncode != 0 or not dest.is_file():
            if page and not system:
                dest.unlink(missing_ok=True)
                await run_in_threadpool(shutil.move, str(page), str(dest))  # the original is still playable
            else:
                print("[record] mix failed, parts kept:", (res.stderr or "")[-300:], flush=True)
                dest.unlink(missing_ok=True)
                return _salvage_parts(payload.title.strip() or "Запись",
                                      [(p, has_video if p is page else False) for p in (page, system) if p], payload.notes)
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
    from app.database import update_meeting_fields
    from app.naming import rename_recording_file

    if payload.notes.strip():
        update_meeting_fields(m["id"], user_notes=payload.notes[:50000])
    try:
        from datetime import datetime

        from app import calendar_ics

        calendar_ics.apply_to_recording(m["id"], datetime.fromtimestamp(payload.started_at) if payload.started_at > 0 else None)
    except Exception as e:
        print("[calendar]", e, flush=True)
    rename_recording_file(m["id"])  # "2026-10-20 Запись 14-05.m4a"; renamed again once titled
    enqueue_meeting_job(m["id"])
    return {"created": [get_meeting(m["id"]) or m]}

class UiLangPayload(BaseModel):
    lang: str = "ru"

@router.post("/api/ui/lang")
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

@router.post("/api/ui/state")
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

@router.post("/api/live/start")
def api_live_start(payload: LiveStartPayload) -> Dict[str, Any]:
    from app.live import start_session

    target, pair = _live_target(payload.translate_to, payload.pair)
    s = start_session(payload.lang, target, payload.capture_id, payload.speed, pair)
    s.draft = payload.draft
    return s.state()

@router.post("/api/live/{session_id}/audio")
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

@router.post("/api/live/{session_id}/ask")
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

@router.post("/api/live/{session_id}/config")
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

@router.get("/api/live/models")
def api_live_models() -> Dict[str, Any]:
    from app.live_translate import model_choices

    return model_choices()

@router.post("/api/live/{session_id}/stop")
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

@router.post("/api/library/ask")
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
