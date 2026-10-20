"""
Exports that need a job first: a translated document before the translation exists, a dubbed file
before the dub, any export while the recording is still being transcribed.

`request()` either answers "ready" (the page saves the file itself, with the Save dialog) or starts
the missing job and remembers the export on the recording. When a job of that recording finishes,
`process()` writes every export whose requirements are now met into the export folder and tells
the page ({"type": "exported"} event); exports whose job failed are dropped and reported.
"""

import shutil
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import events
from app.database import get_meeting, get_pending_exports, set_pending_exports
from app.export_build import DOC_FORMATS, EXPORT_VARIANTS, build_export_bytes, default_export_dir, unique_path

KINDS = ("doc", "video_soft", "video_burn", "dub_media", "dub_srt", "obsidian")
MAX_TRIES = 1  # a job that failed is not started again for the same export


def normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(item.get("kind") or "doc")
    if kind not in KINDS:
        raise ValueError("Неизвестный вид экспорта")
    fmt = str(item.get("fmt") or "").lower()
    if kind == "doc" and fmt not in DOC_FORMATS:
        raise ValueError(f"Неизвестный формат экспорта: {fmt}")
    lang = (str(item.get("lang") or "").strip() or None)
    variant = str(item.get("variant") or "translated")
    if lang and variant not in EXPORT_VARIANTS:
        raise ValueError("Неизвестный вариант экспорта перевода")
    if kind in ("dub_media", "dub_srt") and not lang:
        raise ValueError("Не указан язык дубляжа")
    if kind == "doc" and lang and fmt == "json":
        raise ValueError("Перевод экспортируется в DOCX, MD, TXT или SRT")
    return {"kind": kind, "fmt": fmt, "lang": lang, "variant": variant, "tries": int(item.get("tries") or 0)}


def _has_translation(m: Dict[str, Any], lang: str) -> bool:
    tr = (m.get("translations") or {}).get(lang)
    return bool(tr) and int(tr.get("translated") or 0) > 0


def _dub(m: Dict[str, Any], lang: str) -> Optional[Dict[str, Any]]:
    for d in m.get("dubs") or []:
        if d.get("lang") == lang and Path(d.get("path") or "").is_file():
            return d
    return None


def missing_step(m: Dict[str, Any], item: Dict[str, Any]) -> Optional[str]:
    """What the export still needs: "transcribe", "translate", "dub" or None (ready)."""
    if m.get("status") != "completed" or not m.get("segments"):
        return "transcribe"
    if item["kind"] in ("dub_media", "dub_srt"):
        return None if _dub(m, item["lang"]) else "dub"
    if item["lang"] and not _has_translation(m, item["lang"]):
        return "translate"
    return None


def _job_running_for(meeting_id: int, kind: Optional[str] = None, lang: Optional[str] = None) -> bool:
    from app import transcriber

    with transcriber._jobs_lock:
        if meeting_id not in transcriber._active_jobs:
            return False
        if kind is None:
            return True
        return any(q["id"] == meeting_id and q["kind"] == kind and (lang is None or q.get("lang") == lang)
                   for q in transcriber._queue)


def _remember(meeting_id: int, item: Dict[str, Any]) -> None:
    items = [x for x in get_pending_exports(meeting_id) if {**x, "tries": 0} != {**item, "tries": 0}]
    items.append(item)
    set_pending_exports(meeting_id, items)


def request(meeting_id: int, raw: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    from app.transcriber import enqueue_translate_job

    item = normalize(raw)
    m = get_meeting(meeting_id)
    if not m:
        raise LookupError("Встреча не найдена")
    step = missing_step(m, item)
    folder = str(default_export_dir())
    if step is None:
        return {"status": "ready"}
    if step == "transcribe":
        if not _job_running_for(meeting_id):
            raise ValueError("Запись не расшифрована: сначала запустите обработку заново")
        _remember(meeting_id, item)
        return {"status": "queued", "waiting_for": "transcribe", "folder": folder}
    if step == "translate":
        if not _job_running_for(meeting_id):
            if not enqueue_translate_job(meeting_id, item["lang"], model, False):
                raise ValueError("Не удалось поставить перевод в очередь")
            item["tries"] += 1
        _remember(meeting_id, item)
        return {"status": "queued", "waiting_for": "translate", "folder": folder}
    # A dub needs the user's consent, so it is started from the Dub window, never from here
    if _job_running_for(meeting_id, "dub", item["lang"]):
        _remember(meeting_id, item)
        return {"status": "queued", "waiting_for": "dub", "folder": folder}
    return {"status": "needs_dub"}


def perform(m: Dict[str, Any], item: Dict[str, Any]) -> Optional[Path]:
    """Writes one export into the export folder; returns the file (None for a queued video)."""
    folder = default_export_dir()
    folder.mkdir(parents=True, exist_ok=True)
    kind = item["kind"]
    if kind == "doc":
        content, _type, name = build_export_bytes(m, item["fmt"], item["lang"], item["variant"])
        out = unique_path(folder, name)
        out.write_bytes(content)
        return out
    if kind in ("dub_media", "dub_srt"):
        d = _dub(m, item["lang"]) or {}
        src = Path(d["path"]) if kind == "dub_media" else Path(d.get("srt_path") or Path(d["path"]).with_suffix(".srt"))
        if not src.is_file():
            raise LookupError("Файл дубляжа не найден")
        out = unique_path(folder, src.name)
        shutil.copy2(src, out)
        return out
    if kind == "obsidian":
        from app.obsidian import export_meeting

        return export_meeting(int(m["id"]), generate=True)
    return _video_subtitles(m, item, folder)


def _video_subtitles(m: Dict[str, Any], item: Dict[str, Any], folder: Path) -> Optional[Path]:
    from app.transcriber import enqueue_burn_subtitles_job
    from app.translation import build_cues, cues_to_srt
    from app.video_subtitles import check_output_path, default_output_path, mux_soft_subtitles, probe_media

    media = Path(m.get("media_path") or "")
    if not media.is_file() or not probe_media(media)["has_video"]:
        raise LookupError("В исходном файле нет видеодорожки")
    lang = item["lang"]
    variant = item["variant"] if lang else "original"
    resolved = (m.get("translations") or {}).get(lang) if lang else None
    cues = build_cues(m.get("segments") or [], resolved, variant=variant, speakers=m.get("speakers"))
    if not cues:
        raise LookupError("Нет текста для субтитров")
    srt_text = cues_to_srt(cues)
    track_lang = lang or (m.get("language") or "")
    mode = "burn" if item["kind"] == "video_burn" else "soft"
    out = default_output_path(m.get("title") or media.stem, media, track_lang or "orig", variant, mode)
    out = unique_path(folder, out.name)
    check_output_path(media, out)
    if mode == "soft":
        title = f"{track_lang.upper()} (AI translation)" if lang else (track_lang.upper() or "Subtitles")
        return mux_soft_subtitles(media, srt_text, track_lang, title, out)
    if not enqueue_burn_subtitles_job(int(m["id"]), track_lang or "orig", srt_text, str(out)):
        raise RuntimeError("Не удалось поставить встраивание субтитров в очередь")
    return None


def process(meeting_id: int) -> None:
    """Called when a job of the recording has finished."""
    from app.transcriber import enqueue_translate_job

    items = get_pending_exports(meeting_id)
    if not items:
        return
    m = get_meeting(meeting_id)
    if not m:
        return
    done: List[str] = []
    queued: List[str] = []
    errors: List[str] = []
    remaining: List[Dict[str, Any]] = []
    busy = _job_running_for(meeting_id)
    for item in items:
        try:
            item = normalize(item)
            step = missing_step(m, item)
            if step is None:
                out = perform(m, item)
                (done if out else queued).append(str(out) if out else item["kind"])
                continue
            if busy:
                remaining.append(item)  # another job of this recording runs first
            elif step == "translate" and item["tries"] < MAX_TRIES and enqueue_translate_job(meeting_id, item["lang"], None, False):
                item["tries"] += 1
                remaining.append(item)
                busy = True
            else:
                errors.append(_failure_text(step, item))
        except Exception as e:
            traceback.print_exc()
            errors.append(str(e)[:200])
    set_pending_exports(meeting_id, remaining)
    if done or errors or queued:
        events.publish(
            {"type": "exported", "id": meeting_id, "files": done, "queued": queued, "errors": errors,
             "folder": str(default_export_dir())},
            key=f"exported:{meeting_id}:{time.time()}",
        )


def _failure_text(step: str, item: Dict[str, Any]) -> str:
    what = {"transcribe": "расшифровка не завершилась", "translate": f"перевод ({(item.get('lang') or '').upper()}) не получился",
            "dub": f"дубляж ({(item.get('lang') or '').upper()}) не получился"}.get(step, step)
    return f"Экспорт не выполнен: {what}"


def cancel_all(meeting_id: int) -> None:
    set_pending_exports(meeting_id, [])

