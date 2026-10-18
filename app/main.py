import mimetypes
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.database import (
    BASE_DIR,
    UPLOADS_DIR,
    add_dictionary_term,
    create_meeting,
    delete_dictionary_term,
    delete_meeting,
    get_all_settings,
    get_meeting,
    init_db,
    list_dictionary,
    list_meetings,
    save_meeting_results,
    toggle_dictionary_term,
    update_meeting_progress,
    update_settings,
)
from app.exporter import export_docx, export_json, export_markdown, export_srt, export_txt
from app.hardware import get_hardware_info
from app.ollama_client import generate_meeting_summary, get_ollama_status, start_model_pull
from app.transcriber import enqueue_meeting_job

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

app = FastAPI(title="VerbaScribe", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    # Automatically resume any recordings that were interrupted mid-processing
    active_states = {"queued", "extracting_audio", "transcribing", "diarizing", "summarizing"}
    for m in list_meetings():
        if m.get("status") in active_states:
            enqueue_meeting_job(int(m["id"]))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>UI loading...</h1>")
    return HTMLResponse(
        index_file.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
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
    }


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
    updated = update_settings(payload.settings)
    return {"settings": updated, "ollama": get_ollama_status()}


@app.get("/api/ollama/status")
def api_ollama_status() -> Dict[str, Any]:
    return get_ollama_status()


class OllamaPullPayload(BaseModel):
    model: str


@app.post("/api/ollama/pull")
def api_ollama_pull(payload: OllamaPullPayload) -> Dict[str, Any]:
    return start_model_pull(payload.model.strip())


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


@app.delete("/api/dictionary/{term_id}")
def api_delete_dictionary(term_id: int) -> Dict[str, Any]:
    delete_dictionary_term(term_id)
    return {"ok": True}


# --- Meetings & Batch Processing ---
@app.get("/api/meetings")
def api_list_meetings() -> List[Dict[str, Any]]:
    return list_meetings()


@app.get("/api/meetings/search")
def api_search_meetings(q: str = "") -> List[Dict[str, Any]]:
    from app.database import search_all_meetings

    return search_all_meetings(q)


@app.get("/api/meetings/{meeting_id}")
def api_get_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return m


@app.post("/api/meetings/upload")
async def api_upload_meetings(
    files: List[UploadFile] = File(...),
    language: str = Form("auto"),
    model_name: str = Form(""),
    num_speakers: int = Form(0),
) -> Dict[str, Any]:
    settings = get_all_settings()
    chosen_model = model_name.strip() or settings.get("whisper_model", "large-v3-turbo")
    chosen_lang = language.strip() or settings.get("language", "auto")
    chosen_speakers = num_speakers if num_speakers > 0 else int(settings.get("num_speakers", 0))

    created = []
    for uf in files:
        orig_name = uf.filename or "recording.mp4"
        ext = Path(orig_name).suffix.lower() or ".mp4"
        safe_stem = Path(orig_name).stem
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
        dest_path = UPLOADS_DIR / unique_name

        with dest_path.open("wb") as out_f:
            shutil.copyfileobj(uf.file, out_f)

        m = create_meeting(
            title=safe_stem,
            filename=orig_name,
            media_path=str(dest_path),
            language=chosen_lang,
            model_name=chosen_model,
            num_speakers_hint=chosen_speakers,
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
        )
        enqueue_meeting_job(m["id"])
        created.append(m)

    return {"created": created}


class MeetingTitlePayload(BaseModel):
    title: str


@app.patch("/api/meetings/{meeting_id}/title")
def api_rename_meeting(meeting_id: int, payload: MeetingTitlePayload) -> Dict[str, Any]:
    from app.database import _db_lock, get_connection

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))
        conn.commit()
        conn.close()
    return get_meeting(meeting_id)


@app.post("/api/meetings/{meeting_id}/retry")
def api_retry_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
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
    from app.database import _db_lock, get_connection

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    with _db_lock:
        conn = get_connection()
        conn.execute(
            """
            UPDATE meetings
            SET model_name = ?, language = ?, num_speakers_hint = ?,
                status = 'queued', progress = 0,
                stage_text = 'Повторная обработка с новыми параметрами...', error_message = ''
            WHERE id = ?
            """,
            (
                payload.model_name.strip() or "large-v3-turbo",
                payload.language.strip() or "auto",
                int(payload.num_speakers_hint),
                meeting_id,
            ),
        )
        conn.commit()
        conn.close()

    enqueue_meeting_job(meeting_id)
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
def api_delete_meeting(meeting_id: int) -> Dict[str, Any]:
    ok = delete_meeting(meeting_id)
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

    speakers = m.get("speakers") or {}
    if payload.speaker_id not in speakers:
        speakers[payload.speaker_id] = {
            "id": payload.speaker_id,
            "name": payload.name.strip() or payload.speaker_id,
            "color": payload.color or "#6366f1",
            "sample_start": 0.0,
            "sample_end": 5.0,
        }
    else:
        speakers[payload.speaker_id]["name"] = payload.name.strip() or payload.speaker_id
        if payload.color:
            speakers[payload.speaker_id]["color"] = payload.color

    save_meeting_results(meeting_id, speakers=speakers)
    return get_meeting(meeting_id)


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

    segments = m.get("segments") or []
    found = False
    for seg in segments:
        if int(seg.get("id", -1)) == segment_id:
            if payload.text is not None:
                seg["text"] = payload.text.strip()
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


# --- AI Summary & Custom Q&A via Ollama ---
class SummaryRequestPayload(BaseModel):
    template: str = "protocol"
    custom_prompt: str = ""
    model: Optional[str] = None


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
        raise HTTPException(status_code=400, detail="Нет текста встречи для генерации саммари")

    # Free any cached speech models from VRAM before invoking local LLM
    unload_diarizer()
    clear_vram()

    try:
        result_md = generate_meeting_summary(
            segments=segments,
            speakers=speakers,
            template_key=payload.template,
            custom_prompt=payload.custom_prompt,
            model_name=payload.model,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    summaries = m.get("summaries") or {}
    key = "custom" if payload.custom_prompt.strip() else payload.template
    summaries[key] = result_md
    summaries.pop("error", None)
    save_meeting_results(meeting_id, summaries=summaries)
    return {"key": key, "summary": result_md, "summaries": summaries}


# --- Media Streaming for Interactive Player ---
@app.get("/api/meetings/{meeting_id}/media")
def api_stream_media(meeting_id: int) -> FileResponse:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    media_path = Path(m.get("media_path", ""))
    audio_path = Path(m.get("audio_path", ""))

    target = media_path if media_path.exists() else audio_path
    if not target or not target.exists():
        raise HTTPException(status_code=404, detail="Медиафайл не найден")

    mime_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        path=str(target),
        media_type=mime_type or "application/octet-stream",
        filename=target.name,
    )


# --- Multi-format Export ---
def _build_export_bytes(m: Dict[str, Any], fmt: str) -> tuple[bytes, str, str]:
    safe_title = (
        "".join(c if c.isalnum() or c in " -_" else "_" for c in m.get("title", "meeting")).strip()
        or "meeting"
    )
    fmt = fmt.lower()
    if fmt == "md":
        return export_markdown(m).encode("utf-8"), "text/markdown; charset=utf-8", f"{safe_title}.md"
    if fmt == "txt":
        return export_txt(m).encode("utf-8"), "text/plain; charset=utf-8", f"{safe_title}.txt"
    if fmt == "srt":
        return export_srt(m).encode("utf-8"), "text/plain; charset=utf-8", f"{safe_title}.srt"
    if fmt == "json":
        return export_json(m).encode("utf-8"), "application/json; charset=utf-8", f"{safe_title}.json"
    if fmt == "docx":
        return (
            export_docx(m),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"{safe_title}.docx",
        )
    raise HTTPException(status_code=400, detail=f"Неизвестный формат экспорта: {fmt}")


@app.get("/api/meetings/{meeting_id}/export/{fmt}")
def api_export_meeting(meeting_id: int, fmt: str) -> Response:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    content, media_type, filename = _build_export_bytes(m, fmt)
    encoded_filename = quote(filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        "Access-Control-Expose-Headers": "Content-Disposition",
    }
    return Response(content=content, media_type=media_type, headers=headers)


_EXPORT_FILE_TYPES = {
    "md": ("Markdown (*.md)", "*.md", ".md"),
    "txt": ("Текстовый файл (*.txt)", "*.txt", ".txt"),
    "srt": ("Субтитры (*.srt)", "*.srt", ".srt"),
    "json": ("JSON (*.json)", "*.json", ".json"),
    "docx": ("Документ Word (*.docx)", "*.docx", ".docx"),
}


def _default_export_dir() -> Path:
    """Returns the last used export folder, or the user's Documents folder."""
    saved = (get_all_settings().get("export_dir") or "").strip()
    if saved and Path(saved).is_dir():
        return Path(saved)
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


def _pick_path_native(mode: str, directory: Path, filename: str = "", fmt: str = "") -> Optional[str]:
    """
    Spawns an isolated native file/folder dialog via app.native_dialog in a subprocess.
    This guarantees 100% thread safety and prevents any deadlocks with pywebview or uvicorn.
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
def api_save_export_to_disk(meeting_id: int, fmt: str) -> Dict[str, Any]:
    """
    Asks the user where to save the export via a native Save dialog,
    writes the file there and remembers the chosen folder for next time.
    """
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    content, _, filename = _build_export_bytes(m, fmt)
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

