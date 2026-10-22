"""The player, dubbing, VRAM chips, updates, translation, cuts and subtitles."""

import mimetypes
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.database import get_meeting
from app.transcriber import enqueue_burn_subtitles_job, enqueue_dub_job, enqueue_translate_job

from fastapi import APIRouter

from app.main import (  # noqa: E402 - shared helpers live in app/main.py
    _EXPORT_VARIANTS, _ensure_not_processing, _pick_export_path, _remember_export,
)

router = APIRouter()

# --- Media Streaming for Interactive Player ---
@router.get("/api/meetings/{meeting_id}/media")
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

@router.get("/api/dub/status")
def api_dub_status() -> Dict[str, Any]:
    from app.dubbing import dub_environment_status

    return dub_environment_status()

@router.get("/api/queue")
def api_queue() -> List[Dict[str, Any]]:
    """Processing queue: the running job first, then waiting jobs in order."""
    from app.transcriber import queue_snapshot

    return queue_snapshot()

@router.post("/api/dub/setup")
def api_dub_setup() -> Dict[str, Any]:
    """Installs the optional dubbing environment (.venv-dub) after the user confirmed it in the UI."""
    from app.dub_setup import start_setup

    return start_setup()

@router.post("/api/dub/setup/cancel")
def api_dub_setup_cancel() -> Dict[str, Any]:
    from app.dub_setup import cancel_setup

    return cancel_setup()

def _dub_job_running() -> bool:
    from app.transcriber import running_job_kind

    return running_job_kind() == "dub"

@router.get("/api/dub/info")
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

@router.post("/api/dub/verify")
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

@router.post("/api/dub/weights/download")
def api_dub_weights_download(payload: DubWeightsPayload) -> Dict[str, Any]:
    from app.dub_models import start_download

    return start_download(payload.models, _dub_job_running())

@router.post("/api/dub/weights/cancel")
def api_dub_weights_cancel() -> Dict[str, Any]:
    from app.dub_models import cancel_download

    return cancel_download()

@router.delete("/api/dub/weights/{model_id}")
def api_dub_weights_delete(model_id: str) -> Dict[str, Any]:
    from app.dub_models import delete_weights

    try:
        return delete_weights(model_id, _dub_job_running())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

# --- Models in VRAM (chips next to the GPU badge) ---
@router.get("/api/vram")
def api_vram() -> Dict[str, Any]:
    from app.vram import vram_snapshot

    return vram_snapshot()

class VramActionPayload(BaseModel):
    kind: str
    name: str = ""

@router.post("/api/vram/unload")
def api_vram_unload(payload: VramActionPayload) -> Dict[str, Any]:
    from app.vram import unload

    try:
        return unload(payload.kind, payload.name)
    except (RuntimeError, ValueError, httpx.HTTPError) as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.post("/api/vram/preload")
def api_vram_preload(payload: VramActionPayload) -> Dict[str, Any]:
    from app.vram import preload

    try:
        return preload(payload.kind, payload.name)
    except (RuntimeError, ValueError, httpx.HTTPError) as e:
        raise HTTPException(status_code=409, detail=str(e))

# --- Updates from GitHub Releases (app/updater.py) ---
@router.get("/api/update")
def api_update_status(check: bool = False) -> Dict[str, Any]:
    from app import updater

    return updater.check(force=False) if check else updater.status()

@router.post("/api/update/check")
def api_update_check() -> Dict[str, Any]:
    from app import updater

    return updater.check(force=True)

@router.post("/api/update/download")
def api_update_download() -> Dict[str, Any]:
    from app import updater

    try:
        return updater.start_download()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.post("/api/update/install")
def api_update_install() -> Dict[str, Any]:
    from app import updater
    from app.transcriber import running_job_kind

    if running_job_kind():
        raise HTTPException(status_code=409, detail="Дождитесь окончания текущей задачи")
    from app import app_status

    st = app_status.snapshot()
    if st["recording"] or st["state"] != "idle":  # the app quits for the update: nothing may be cut off
        raise HTTPException(status_code=409, detail=f"Дождитесь окончания: {st['text']}")
    try:
        updater.launch_installer()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    updater.quit_soon()
    return {"ok": True}

@router.post("/api/meetings/{meeting_id}/dub")
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
    from app.dubbing import dub_memory_gb
    from app.sysinfo import memory

    need, ram = dub_memory_gb(float(m.get("duration") or 0)), (memory() or {}).get("total_gb")
    if ram and need > ram * 0.8:  # it would run out of memory hours into the job
        raise HTTPException(status_code=409, detail=(
            f"Для дубляжа записи длиной {float(m.get('duration') or 0) / 3600:.1f} ч нужно около {need:.0f} ГБ памяти, "
            f"а в компьютере {ram:.0f} ГБ. Сделайте дубляж частей записи (Нарезка) или более короткой записи."))
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

@router.get("/api/meetings/{meeting_id}/dub/{lang}/file")
def api_dub_file(meeting_id: int, lang: str) -> FileResponse:
    d = _find_dub(meeting_id, lang)
    target = Path(d["path"])
    mime_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(path=str(target), media_type=mime_type or "application/octet-stream", filename=target.name)

@router.post("/api/meetings/{meeting_id}/dub/{lang}/save/{kind}")
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
    chosen = _pick_export_path(get_meeting(meeting_id), src.name, fmt)
    if not chosen:
        return {"ok": False, "cancelled": True}
    out_path = Path(chosen)
    if out_path.suffix.lower() != src.suffix.lower():
        out_path = out_path.with_name(out_path.name + src.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out_path)
    _remember_export(meeting_id, out_path)
    return {"ok": True, "path": str(out_path), "filename": out_path.name, "folder": str(out_path.parent)}

@router.post("/api/meetings/{meeting_id}/dub/{lang}/reveal")
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

@router.get("/api/translate/languages")
def api_translate_languages() -> Dict[str, Any]:
    from app.translation import TEXT_LANGUAGES

    return {"languages": list(TEXT_LANGUAGES.keys())}

@router.post("/api/meetings/{meeting_id}/translate")
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

@router.delete("/api/meetings/{meeting_id}/translations/{lang}")
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
    kind: str = "clean"  # "clean": no pauses and fillers; "highlights": the key moments; "denoise": clean sound
    lang: str = "ru"
    model: Optional[str] = None

@router.post("/api/meetings/{meeting_id}/cut")
def api_meeting_cut(meeting_id: int, payload: CutPayload) -> Dict[str, Any]:
    """A shorter copy of the recording (app/media_cut.py), saved where the user picks."""
    from app.media_cut import kept_seconds, merge, pick_highlights, speech_intervals
    from app.naming import base_name
    from app.transcriber import enqueue_cut_job
    from app.video_subtitles import output_extension, probe_media

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if payload.kind not in ("clean", "highlights", "denoise"):
        raise HTTPException(status_code=400, detail="Неизвестный вид нарезки")
    _ensure_not_processing(meeting_id)
    if payload.kind != "denoise" and (m.get("status") != "completed" or not m.get("segments")):
        raise HTTPException(status_code=400, detail="Нарезка доступна после полной расшифровки записи")
    media = Path(m.get("media_path") or "")
    if not media.is_file():
        raise HTTPException(status_code=404, detail="Исходный файл не найден")
    duration = float(m.get("duration") or 0)
    moments: List[Dict[str, Any]] = []
    if payload.kind == "denoise":
        if duration <= 0:
            from app.audio_utils import get_media_duration

            duration = float(get_media_duration(media) or 0)
        intervals = [(0.0, duration)] if duration > 0 else []
    elif payload.kind == "clean":
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
    suffix = {"highlights": ".highlights", "denoise": ".clean-sound"}.get(payload.kind, ".clean")
    chosen = _pick_export_path(m, f"{base_name(m)}{suffix}{ext}", ext.lstrip("."))
    if not chosen:
        return {"ok": False, "cancelled": True}
    out = Path(chosen)
    if out.suffix.lower() != ext:
        out = out.with_name(out.name + ext)
    if out.resolve() == media.resolve():
        raise HTTPException(status_code=400, detail="Нельзя сохранить результат поверх исходного файла")
    _remember_export(meeting_id, out)
    if not enqueue_cut_job(meeting_id, payload.kind, intervals, has_video, str(out)):
        raise HTTPException(status_code=409, detail="Запись сейчас обрабатывается")
    return {"ok": True, "queued": True, "filename": out.name, "path": str(out), "kept_seconds": round(kept_seconds(intervals), 1),
            "duration": duration, "moments": moments}

@router.post("/api/meetings/{meeting_id}/subtitles")
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
    if m.get("status") != "completed" or not m.get("segments"):
        raise HTTPException(status_code=400, detail="Субтитры доступны после полной расшифровки записи")
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
        chosen = _pick_export_path(m, out_path.name, ext.lstrip("."))
        if not chosen:
            return {"ok": False, "cancelled": True}
        out_path = Path(chosen)
        if out_path.suffix.lower() != ext:
            out_path = out_path.with_name(out_path.name + ext)
        _remember_export(meeting_id, out_path)
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

@router.get("/api/meetings/{meeting_id}/peaks")
def api_media_peaks(meeting_id: int, bars: int = 120) -> Dict[str, Any]:
    """Real loudness envelope of the recording for the player waveform: from the 16 kHz WAV, or from
    the envelope kept when the WAV was deleted after processing."""
    import json

    from app.audio_utils import loudness_peaks, peaks_file, resample_peaks

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    bars = max(20, min(400, int(bars)))
    wav = Path(m.get("audio_path") or "")
    if wav.is_file():
        try:
            return {"peaks": loudness_peaks(wav, bars)}
        except Exception:
            return {"peaks": []}
    try:
        kept = json.loads(peaks_file(meeting_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # No envelope kept: a recording whose own file is a WAV (a dictation) is read directly
        media = Path(m.get("media_path") or "")
        if media.suffix.lower() == ".wav" and media.is_file():
            try:
                return {"peaks": loudness_peaks(media, bars)}
            except Exception:
                pass
        return {"peaks": []}
    return {"peaks": resample_peaks([float(x) for x in kept], bars)}
