"""The library: recordings, search, import, backup, comparison, calendar, the GPU build."""

import re
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from app import __version__
from app.paths import UPLOADS_DIR
from app.settings_schema import as_int
from app.database import create_meeting, delete_meeting, edit_meeting, get_all_settings, get_meeting, is_local_only, list_meetings, update_meeting_fields, update_meeting_progress, update_settings
from app.transcriber import enqueue_meeting_job, is_job_active, request_cancel

from fastapi import APIRouter

from app.main import (  # noqa: E402 - shared helpers live in app/main.py
    DISK_STOP_BYTES, SUPPORTED_EXTENSIONS, _COLOR_RE, _SPEAKER_ID_RE, _ensure_not_processing, _ensure_not_transcribing, _file_time, _free_bytes, _local_time, _recordings_dir, api_get_meeting, disk_state,
)

router = APIRouter()

# --- Meetings & Batch Processing ---
@router.get("/api/meetings")
def api_list_meetings() -> List[Dict[str, Any]]:
    return list_meetings()

@router.get("/api/meetings/search")
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

@router.get("/api/semantic/status")
def api_semantic_status() -> Dict[str, Any]:
    from app import semantic

    return semantic.status()

@router.post("/api/semantic/index")
def api_semantic_index() -> Dict[str, Any]:
    """Builds the meaning index now (an explicit action: Ollama may be started for it)."""
    from app import semantic
    from app.ollama_client import ensure_ollama_running

    settings = get_all_settings()
    if not ensure_ollama_running((settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1")):
        raise HTTPException(status_code=503, detail="Ollama не запущена: установите её в «Настройки → Анализ»")
    semantic.start_indexer()
    return semantic.status()

router.get('/api/meetings/{meeting_id}')(api_get_meeting)

@router.get("/api/meetings/{meeting_id}/llm-estimate")
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

@router.patch("/api/meetings/{meeting_id}/local-only")
def api_meeting_local_only(meeting_id: int, payload: LocalOnlyPayload) -> Dict[str, Any]:
    """Marks a recording "local analysis only": it is never sent to a cloud model."""
    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    update_meeting_fields(meeting_id, local_only=1 if payload.local_only else 0)
    return api_get_meeting(meeting_id)

@router.get("/api/calls/status")
def api_calls_status() -> Dict[str, Any]:
    from app import call_watch

    return call_watch.status()

# --- Backup of the library (app/backup.py) ---
def _backup_dialog(mode: str, filename: str = "") -> Optional[str]:
    import subprocess

    cmd = [sys.executable, "-m", "app.native_dialog", "--mode", mode, "--dir", str(Path.home() / "Documents"),
           "--filename", filename, "--filter", "Резервная копия VerbaScribe (*.zip)|*.zip", "--ext", ".zip",
           "--title", "Сохранить резервную копию" if mode == "save" else "Восстановить из резервной копии"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
        return (proc.stdout or "").strip() or None
    except Exception as e:
        print(f"[backup] dialog failed: {e}", flush=True)
        return None

@router.get("/api/backup")
def api_backup_estimate() -> Dict[str, Any]:
    from app import backup

    return backup.estimate()

class BackupPayload(BaseModel):
    with_media: bool = False

@router.post("/api/backup")
async def api_backup_make(payload: BackupPayload) -> Dict[str, Any]:
    from datetime import datetime

    from app import backup

    name = f"VerbaScribe {datetime.now().strftime('%Y-%m-%d')}{' с медиа' if payload.with_media else ''}.zip"
    dest = await run_in_threadpool(_backup_dialog, "save", name)
    if not dest:
        return {"ok": False, "cancelled": True}
    try:
        return {"ok": True, **await run_in_threadpool(backup.make, Path(dest), payload.with_media)}
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить копию: {e}")

@router.post("/api/backup/restore")
async def api_backup_restore() -> Dict[str, Any]:
    import zipfile

    from app import backup

    src = await run_in_threadpool(_backup_dialog, "open")
    if not src:
        return {"ok": False, "cancelled": True}
    try:
        return {"ok": True, **await run_in_threadpool(backup.restore, Path(src))}
    except (ValueError, OSError, zipfile.BadZipFile) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/about")
def api_about() -> Dict[str, Any]:
    """The About window: version, and how much the program has transcribed on this computer."""
    done = [m for m in list_meetings() if m.get("status") == "completed"]
    firsts = sorted(str(m.get("created_at") or "")[:10] for m in done if m.get("created_at"))
    return {"version": __version__, "repo": "pravken/VerbaScribe", "recordings": len(done),
            "hours": round(sum(float(m.get("duration") or 0) for m in done) / 3600, 1),
            "since": firsts[0] if firsts else ""}

@router.get("/api/hotkey")
def api_hotkey() -> Dict[str, Any]:
    """Whether the recording hotkey is registered (it lives in the desktop window's tray thread)."""
    from app.hotkey import dictation_hotkey_state, hotkey_state

    return {**hotkey_state, "dictation": dict(dictation_hotkey_state)}

@router.get("/api/calendar/upcoming")
def api_calendar_upcoming(refresh: bool = False) -> Dict[str, Any]:
    """The next meetings of the user's calendar (Settings -> General -> Calendar), with the series they continue."""
    from app import calendar_ics, series

    if refresh:
        calendar_ics.events(force=True)
    items = calendar_ics.upcoming()
    done = sorted((r for r in list_meetings() if r.get("status") == "completed"),
                  key=lambda r: str(r.get("recorded_at") or r.get("created_at") or ""), reverse=True)
    known = {n for r in done for n in series.people(r)}
    for it in items:
        key = series.title_key(it["title"])
        same = next((r for r in done if r.get("series_id") and len(key) >= 6 and series.title_key(r.get("title") or "") == key), None)
        it["series_id"] = same.get("series_id") if same else None
        it["person"] = next((a for a in it["attendees"] if a.strip().lower() in known), None)
    from app import calendar_outlook

    return {"configured": bool((get_all_settings().get("calendar_ics") or "").strip()), "events": items,
            "outlook": calendar_outlook.supported(), **calendar_ics.status()}

# --- GigaAM / Parakeet on the video card (app/onnx_gpu.py) ---
@router.get("/api/onnx-gpu")
def api_onnx_gpu() -> Dict[str, Any]:
    from app import onnx_gpu

    return onnx_gpu.status()

@router.post("/api/onnx-gpu/install")
def api_onnx_gpu_install() -> Dict[str, Any]:
    from app import onnx_gpu

    if not onnx_gpu.nvidia_present():
        raise HTTPException(status_code=400, detail="Нужна видеокарта NVIDIA")
    return onnx_gpu.install_async()

@router.post("/api/onnx-gpu/remove")
def api_onnx_gpu_remove() -> Dict[str, Any]:
    from app import onnx_gpu

    onnx_gpu.remove()
    update_settings({"onnx_gpu": "false"})
    return onnx_gpu.status()

# --- Model comparison on the user's recordings (app/bench.py) ---
class BenchPayload(BaseModel):
    kind: str = "asr"          # asr | llm
    meeting_id: int
    models: List[str] = []
    clip_sec: int = 180        # asr: seconds of the recording, 0: all of it

@router.get("/api/bench")
def api_bench(choices: bool = False) -> Dict[str, Any]:
    from app import bench

    out = bench.status()
    if choices:
        out["asr_models"] = bench.asr_choices()
        try:
            out["llm_models"] = bench.llm_choices()
        except Exception:
            out["llm_models"] = []
        out["busy"] = bench.busy_reason()
    return out

@router.post("/api/bench/start")
def api_bench_start(payload: BenchPayload) -> Dict[str, Any]:
    from app import bench

    try:
        return bench.start(payload.kind, payload.meeting_id, payload.models, payload.clip_sec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/bench/cancel")
def api_bench_cancel() -> Dict[str, Any]:
    from app import bench

    bench.cancel()
    return bench.status()

@router.delete("/api/bench/history")
def api_bench_history_clear() -> Dict[str, Any]:
    from app import bench

    bench.clear_history()
    return bench.status()

@router.get("/api/disk")
def api_disk() -> Dict[str, Any]:
    return disk_state()

_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

@router.post("/api/meetings/upload")
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
    chosen_speakers = num_speakers if num_speakers > 0 else as_int(settings.get("num_speakers"), 0)
    is_recording = source == "recording"
    target_dir = _recordings_dir() if is_recording else UPLOADS_DIR

    created = []
    for i, uf in enumerate(files):
        orig_name = uf.filename or "recording.mp4"
        ext = Path(orig_name).suffix.lower() or ".mp4"
        safe_stem = _UNSAFE_NAME_RE.sub("_", Path(orig_name).stem)[:120] or "recording"
        dest_path = target_dir / f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"

        def _save(src=uf.file, dst=dest_path) -> None:
            with dst.open("wb") as out_f:
                shutil.copyfileobj(src, out_f, 1 << 20)

        # Room for the file, and the reserve that keeps recordings and the database working
        free = _free_bytes(target_dir)
        if uf.size and free >= 0 and free - uf.size < DISK_STOP_BYTES:
            raise HTTPException(status_code=507, detail=f"Недостаточно места на диске для «{orig_name}»: "
                                                        f"нужно {uf.size / 2**30:.1f} ГБ и запас")
        # A multi-GB file must not stop the server (events, other requests) while it is copied
        try:
            await run_in_threadpool(_save)
        except OSError as e:
            dest_path.unlink(missing_ok=True)  # no half file left behind
            raise HTTPException(status_code=507, detail=f"Не удалось сохранить «{orig_name}»: {e}")

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

class UrlImportPayload(BaseModel):
    url: str
    language: str = "auto"
    model_name: str = ""
    num_speakers: int = 0

@router.post("/api/meetings/url")
def api_import_url(payload: UrlImportPayload) -> Dict[str, Any]:
    """A video or podcast by its link (app/url_import.py): downloaded, then transcribed."""
    from app import url_import

    settings = get_all_settings()
    try:
        m = url_import.start(payload.url.strip(), payload.model_name or settings.get("whisper_model") or "large-v3-turbo",
                             payload.language or "auto", int(payload.num_speakers or 0))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"created": [m]}

class LocalPathPayload(BaseModel):
    path: str
    language: str = "auto"
    model_name: str = ""
    num_speakers: int = 0

@router.post("/api/meetings/local-path")
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
    chosen_speakers = payload.num_speakers if payload.num_speakers > 0 else as_int(settings.get("num_speakers"), 0)

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

@router.post("/api/meetings/{meeting_id}/title/generate")
def api_generate_title(meeting_id: int) -> Dict[str, Any]:
    """A title made from the transcript by the analysis model (for several recordings at once)."""
    from app.database import is_local_only
    from app.llm import LlmError
    from app.naming import rename_recording_file
    from app.obsidian import refresh_if_exported
    from app.ollama_client import generate_meeting_title

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    lang = m.get("language") if m.get("language") not in (None, "", "auto") else "ru"
    try:
        title = generate_meeting_title(m.get("segments") or [], m.get("speakers") or {}, answer_lang=lang,
                                       local_only=is_local_only(m))
    except (LlmError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)[:300])
    update_meeting_fields(meeting_id, title=title[:300], title_is_default=0)
    rename_recording_file(meeting_id)
    refresh_if_exported(meeting_id)
    return get_meeting(meeting_id)

@router.patch("/api/meetings/{meeting_id}/title")
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

@router.post("/api/meetings/{meeting_id}/retry")
def api_retry_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    _ensure_not_processing(meeting_id)
    from app import url_import

    if not m.get("media_path") and url_import.valid_url(m.get("filename") or ""):
        # A download by link that did not finish: downloaded again
        url_import.restart(meeting_id, m["filename"])
        return get_meeting(meeting_id)
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

@router.post("/api/meetings/{meeting_id}/reconfigure")
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

@router.post("/api/meetings/{meeting_id}/cancel")
def api_cancel_meeting(meeting_id: int) -> Dict[str, Any]:
    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    from app import url_import

    if not request_cancel(meeting_id) and not url_import.cancel(meeting_id):
        raise HTTPException(status_code=409, detail="Запись сейчас не обрабатывается")
    update_meeting_progress(meeting_id, stage_text="Отмена обработки...")
    return get_meeting(meeting_id)

@router.post("/api/meetings/{meeting_id}/reveal")
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

@router.delete("/api/meetings/{meeting_id}")
def api_delete_meeting(meeting_id: int, with_source: bool = False, with_exports: bool = False,
                       with_note: bool = False) -> Dict[str, Any]:
    if is_job_active(meeting_id):
        request_cancel(meeting_id)
        raise HTTPException(status_code=409, detail="Запись обрабатывается: обработка отменяется, удалите её через несколько секунд.")
    from app import url_import

    url_import.cancel(meeting_id)  # a download of it stops and its file is removed
    ok = delete_meeting(meeting_id, with_source=with_source, with_exports=with_exports, with_note=with_note)
    if not ok:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return {"ok": True}

# --- Speaker Renaming & Segment Editing ---
class SpeakerUpdatePayload(BaseModel):
    speaker_id: str
    name: str
    color: Optional[str] = None
    dismiss_suggestion: bool = False  # "not this person": the suggestion of a known voice is dropped

@router.patch("/api/meetings/{meeting_id}/speakers")
def api_update_speaker(meeting_id: int, payload: SpeakerUpdatePayload) -> Dict[str, Any]:
    _ensure_not_transcribing(meeting_id)
    if not _SPEAKER_ID_RE.match(payload.speaker_id):
        raise HTTPException(status_code=400, detail="Некорректный идентификатор участника")
    if payload.color and not _COLOR_RE.match(payload.color):
        raise HTTPException(status_code=400, detail="Цвет должен быть в формате #RRGGBB")
    name = payload.name.strip()[:120]
    learned: List[Any] = []  # the voice to teach its profile, after the change (it uses the database)

    def change(m: Dict[str, Any]) -> tuple:
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
            spk = speakers[payload.speaker_id]
            old_name = spk.get("name") or ""
            spk["name"] = name or payload.speaker_id
            if payload.color:
                spk["color"] = payload.color
            if name and name != old_name:
                learned.append(spk.get("embedding"))
                spk.pop("voice_profile", None)
            if payload.dismiss_suggestion or (name and name != old_name):
                spk.pop("voice_suggest", None)
        m["speakers"] = speakers
        return ("speakers",)

    m = edit_meeting(meeting_id, change)
    if m is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if learned:
        # Naming a voice teaches its profile: later recordings get the name automatically
        from app.voice_profiles import learn

        try:
            learn(name, learned[0])
        except Exception:
            traceback.print_exc()
    return m
