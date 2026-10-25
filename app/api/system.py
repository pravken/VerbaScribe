"""System, models, settings, dictionary and live progress (SSE)."""

import json
import sys
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app import events, paths
from app.paths import DATA_DIR, MODELS_DIR, UPLOADS_DIR
from app.database import add_dictionary_term, delete_dictionary_term, get_all_settings, list_dictionary, toggle_dictionary_term, update_settings
from app.ffmpeg_manager import ffmpeg_status, start_ffmpeg_download
from app.hardware import get_hardware_info
from app.ollama_client import get_ollama_status, start_model_pull

from fastapi import APIRouter

from app.main import (  # noqa: E402 - shared helpers live in app/main.py
    _recordings_dir,
)

router = APIRouter()

# --- System, Hardware & Settings ---
def windows_text_scale() -> Optional[int]:
    """«Make text bigger» of Windows (Accessibility → Text size), in percent; None elsewhere or unset.
    The page takes its first interface scale from it (audit 5.5)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Accessibility") as key:
            value, _ = winreg.QueryValueEx(key, "TextScaleFactor")
        return int(value)
    except (OSError, ValueError, TypeError):
        return None


@router.get("/api/system")
def api_system_info() -> Dict[str, Any]:
    from app.model_manager import get_models_status

    return {
        "text_scale": windows_text_scale(),
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

@router.get("/api/ffmpeg/status")
def api_ffmpeg_status() -> Dict[str, Any]:
    return ffmpeg_status()

@router.post("/api/ffmpeg/install")
def api_ffmpeg_install() -> Dict[str, Any]:
    """Downloads the pinned FFmpeg build into the user data folder (only on explicit request)."""
    return start_ffmpeg_download()

@router.post("/api/onnx-models/{model_id}/download")
def api_onnx_download(model_id: str) -> Dict[str, Any]:
    """GigaAM / Parakeet (app/asr_onnx.py): downloaded in the background."""
    from app import asr_onnx

    try:
        return asr_onnx.start_download(model_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/api/onnx-models/{model_id}")
def api_onnx_delete(model_id: str) -> Dict[str, Any]:
    from app import asr_onnx

    if not asr_onnx.is_onnx_model(model_id):
        raise HTTPException(status_code=404, detail="Неизвестная модель")
    return {"ok": asr_onnx.delete_model(model_id)}

@router.get("/api/models/status")
def api_models_status() -> Dict[str, Any]:
    from app.model_manager import get_models_status

    return get_models_status()

class ModelsSetupPayload(BaseModel):
    whisper_model: str = "large-v3-turbo"
    install_diarizer: bool = True

@router.post("/api/models/setup")
def api_models_setup(payload: ModelsSetupPayload) -> Dict[str, Any]:
    from app.model_manager import start_models_setup

    return start_models_setup(
        whisper_model_id=payload.whisper_model.strip(),
        install_diarizer=payload.install_diarizer,
    )

@router.delete("/api/models/{model_id}")
def api_delete_whisper_model(model_id: str) -> Dict[str, Any]:
    from app.model_manager import delete_whisper_model, get_models_status
    from app.transcriber import running_job_kind

    if running_job_kind() == "transcribe":
        raise HTTPException(status_code=409, detail="Идёт распознавание — удалите модель после него")
    from app import live

    if live.has_sessions():
        raise HTTPException(status_code=409, detail="Идут живые субтитры — удалите модель после записи")
    worker = live._worker
    if worker is not None and worker.proc is not None:  # an idle caption worker still holds its model files
        with worker.lock:
            worker._kill()
    from app.transcriber import release_whisper_model

    release_whisper_model()
    try:
        delete_whisper_model(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return get_models_status()

class SettingsUpdatePayload(BaseModel):
    settings: Dict[str, Any]

@router.post("/api/settings")
def api_update_settings(payload: SettingsUpdatePayload) -> Dict[str, Any]:
    # Sending transcripts to a cloud service needs the consent given in Settings → Analysis
    if payload.settings.get("llm_provider") == "cloud" and get_all_settings().get("llm_cloud_consent") != "true" \
            and str(payload.settings.get("llm_cloud_consent")) != "true":
        raise HTTPException(status_code=400, detail="Подтвердите отправку текста в облачный сервис в «Настройки → Анализ»")
    from app.settings_schema import clean_all

    try:
        values = clean_all({k: v for k, v in payload.settings.items() if not str(k).startswith("llm_key_")})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    updated = update_settings(values)
    if "start_with_windows" in values:
        from app import autostart

        try:
            autostart.set_enabled(values["start_with_windows"] == "true")
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Не удалось изменить автозапуск Windows: {e}")
    if "semantic_search" in payload.settings or "semantic_model" in payload.settings:
        from app import semantic

        semantic.kick()
    return {"settings": updated, "ollama": get_ollama_status()}

@router.get("/api/ollama/status")
def api_ollama_status() -> Dict[str, Any]:
    return get_ollama_status()

class OllamaPullPayload(BaseModel):
    model: str
    allow_install: bool = False

@router.post("/api/ollama/pull")
def api_ollama_pull(payload: OllamaPullPayload) -> Dict[str, Any]:
    return start_model_pull(payload.model.strip(), allow_install=payload.allow_install)

# --- Custom Dictionary ---
@router.get("/api/dictionary")
def api_list_dictionary() -> List[Dict[str, Any]]:
    return list_dictionary(active_only=False)

class DictionaryCreatePayload(BaseModel):
    term: str
    category: str = "Термин"

@router.post("/api/dictionary")
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

@router.patch("/api/dictionary/{term_id}")
def api_toggle_dictionary(term_id: int, payload: DictionaryTogglePayload) -> Dict[str, Any]:
    toggle_dictionary_term(term_id, payload.active)
    return {"ok": True}

@router.delete("/api/dictionary")
def api_clear_dictionary() -> Dict[str, Any]:
    from app.database import clear_dictionary

    clear_dictionary()
    return {"ok": True}

@router.delete("/api/dictionary/{term_id}")
def api_delete_dictionary(term_id: int) -> Dict[str, Any]:
    delete_dictionary_term(term_id)
    return {"ok": True}

# --- Live progress (Server-Sent Events) ---
_SSE_HEARTBEAT_SEC = 15.0

@router.get("/api/events")
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
