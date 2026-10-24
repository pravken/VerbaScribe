"""Series, voices, segment edits, summaries, promises, notes and templates."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from pydantic import BaseModel
from app.database import edit_meeting, get_meeting, is_local_only, update_meeting_fields
from app.ollama_client import generate_meeting_summary
from app.text_utils import realign_words

from fastapi import APIRouter

from app.main import (  # noqa: E402 - shared helpers live in app/main.py
    _ensure_not_processing, _ensure_not_transcribing, _pick_export_path, _remember_export, api_get_meeting,
)

router = APIRouter()

# --- Series of recurring meetings (app/series.py) ---
@router.get("/api/series")
def api_list_series() -> List[Dict[str, Any]]:
    from app.series import list_series

    return list_series()

class SeriesAssignPayload(BaseModel):
    series_id: Optional[int] = None  # None: out of any series
    new_name: Optional[str] = None   # a new series with this name

@router.patch("/api/meetings/{meeting_id}/series")
def api_meeting_series(meeting_id: int, payload: SeriesAssignPayload) -> Dict[str, Any]:
    from app.series import create_series

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    sid = create_series(payload.new_name) if payload.new_name else payload.series_id
    update_meeting_fields(meeting_id, series_id=sid)
    return api_get_meeting(meeting_id)

class SeriesRenamePayload(BaseModel):
    name: str

@router.patch("/api/series/{series_id}")
def api_rename_series(series_id: int, payload: SeriesRenamePayload) -> Dict[str, Any]:
    from app.series import rename_series

    rename_series(series_id, payload.name)
    return {"ok": True}

class SinceLastPayload(BaseModel):
    lang: str = "ru"
    model: Optional[str] = None

@router.post("/api/meetings/{meeting_id}/since-last")
def api_since_last(meeting_id: int, payload: SinceLastPayload) -> Dict[str, Any]:
    """"Since last time": the previous meeting's tasks checked against this one."""
    from app.series import since_last_time

    try:
        md = since_last_time(meeting_id, payload.model or None, payload.lang or "ru")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)[:300])
    from app.database import merge_summaries

    return {"summaries": merge_summaries(meeting_id, {"since_last": md})}

class BriefPayload(BaseModel):
    series_id: Optional[int] = None
    person: Optional[str] = None
    lang: str = "ru"
    model: Optional[str] = None

@router.post("/api/library/brief")
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

@router.post("/api/library/digest")
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

@router.get("/api/people")
def api_people() -> List[Dict[str, Any]]:
    """Named speakers across the library and the recordings they speak in (app/voice_profiles.py)."""
    from app.voice_profiles import people

    return people()

# --- The people directory ("Все люди", app/people.py) and the speakers of one recording ---
class PersonPayload(BaseModel):
    name: str
    kind: str = "person"

class PersonUpdatePayload(BaseModel):
    name: str
    new_name: Optional[str] = None
    role: Optional[str] = None
    kind: Optional[str] = None
    is_me: Optional[bool] = None

class PeopleMergePayload(BaseModel):
    names: List[str]

@router.get("/api/people/directory")
def api_people_directory() -> List[Dict[str, Any]]:
    from app.people import directory

    return directory()

@router.post("/api/people/directory")
def api_people_add(payload: PersonPayload) -> Dict[str, Any]:
    from app.people import add

    try:
        return add(payload.name, payload.kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/api/people/directory")
def api_people_update(payload: PersonUpdatePayload) -> Dict[str, Any]:
    from app.people import update

    try:
        return update(payload.name, role=payload.role, kind=payload.kind, is_me=payload.is_me, new_name=payload.new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/api/people/merge")
def api_people_merge(payload: PeopleMergePayload) -> Dict[str, Any]:
    from app.people import merge

    try:
        return merge(payload.names)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/api/meetings/{meeting_id}/people")
def api_meeting_people(meeting_id: int) -> Dict[str, Any]:
    """For the speakers of a recording: the calendar's invited people, who is mentioned in the text,
    and the names of the directory (suggestions for a name)."""
    from app.people import calendar_attendees, directory, mentions

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    people = directory()
    speaking = {str((s or {}).get("name") or "").strip().lower() for s in (m.get("speakers") or {}).values()}
    return {
        "calendar": [n for n in calendar_attendees(m) if n.lower() not in speaking],
        "mentions": mentions(m, people),
        "names": [{"name": p["name"], "kind": p["kind"], "role": p["role"], "is_me": p["is_me"]} for p in people],
    }

class SpeakerMergePayload(BaseModel):
    source: str
    target: str

class SpeakerSplitPayload(BaseModel):
    speaker_id: str
    positions: List[int]
    new_name: str = ""

class SpeakerMetaPayload(BaseModel):
    speaker_id: str
    role: Optional[str] = None
    is_me: Optional[bool] = None

def _speaker_edit(meeting_id: int, run) -> Dict[str, Any]:
    _ensure_not_transcribing(meeting_id)
    try:
        m = run()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if m is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return m

@router.post("/api/meetings/{meeting_id}/speakers/merge")
def api_merge_speakers(meeting_id: int, payload: SpeakerMergePayload) -> Dict[str, Any]:
    from app.people import merge_speakers

    return _speaker_edit(meeting_id, lambda: merge_speakers(meeting_id, payload.source, payload.target))

@router.post("/api/meetings/{meeting_id}/speakers/split")
def api_split_speaker(meeting_id: int, payload: SpeakerSplitPayload) -> Dict[str, Any]:
    from app.people import split_speaker

    return _speaker_edit(meeting_id, lambda: split_speaker(meeting_id, payload.speaker_id, payload.positions, payload.new_name))

@router.patch("/api/meetings/{meeting_id}/speakers/meta")
def api_speaker_meta(meeting_id: int, payload: SpeakerMetaPayload) -> Dict[str, Any]:
    from app.people import set_speaker_meta

    return _speaker_edit(meeting_id, lambda: set_speaker_meta(meeting_id, payload.speaker_id, payload.role, payload.is_me))

@router.post("/api/voice-profiles/apply-all")
def api_voice_profiles_apply_all() -> Dict[str, Any]:
    from app.voice_profiles import apply_to_library

    result = apply_to_library()
    if result["meetings"]:
        from app import events

        events.publish({"type": "list"})
    return result

@router.get("/api/voice-profiles")
def api_voice_profiles() -> Dict[str, Any]:
    from app.voice_profiles import enabled, list_profiles

    return {"enabled": enabled(), "profiles": list_profiles()}

@router.delete("/api/voice-profiles/{profile_id}")
def api_delete_voice_profile(profile_id: int) -> Dict[str, Any]:
    from app.voice_profiles import delete_profile, enabled, list_profiles

    if not delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="Голосовой профиль не найден")
    return {"enabled": enabled(), "profiles": list_profiles()}

class SegmentUpdatePayload(BaseModel):
    text: Optional[str] = None
    speaker: Optional[str] = None

@router.patch("/api/meetings/{meeting_id}/segments/{segment_id}")
def api_update_segment(
    meeting_id: int, segment_id: int, payload: SegmentUpdatePayload
) -> Dict[str, Any]:
    _ensure_not_transcribing(meeting_id)
    if payload.text is not None and not payload.text.strip():
        raise HTTPException(status_code=400, detail="Текст реплики не может быть пустым")

    def change(m: Dict[str, Any]) -> tuple:
        if payload.speaker is not None and payload.speaker not in (m.get("speakers") or {}):
            raise HTTPException(status_code=400, detail="Неизвестный участник")
        seg = next((s for s in m.get("segments") or [] if int(s.get("id", -1)) == segment_id), None)
        if seg is None:
            raise HTTPException(status_code=404, detail="Реплика не найдена")
        if payload.text is not None:
            new_text = payload.text.strip()
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
        return ("segments",)

    m = edit_meeting(meeting_id, change)
    if m is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return m

class AlternativePayload(BaseModel):
    word: int
    text: str


@router.post("/api/meetings/{meeting_id}/segments/{segment_id}/alternative")
def api_choose_alternative(meeting_id: int, segment_id: int, payload: AlternativePayload) -> Dict[str, Any]:
    """A disputed word of a merged transcript: another model's variant (app/transcript_merge.py)."""
    from app.transcript_merge import choose_alternative

    _ensure_not_transcribing(meeting_id)

    def change(m: Dict[str, Any]) -> tuple:
        seg = next((s for s in m.get("segments") or [] if int(s.get("id", -1)) == segment_id), None)
        if seg is None:
            raise HTTPException(status_code=404, detail="Реплика не найдена")
        try:
            choose_alternative(seg, payload.word, payload.text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not seg["text"]:
            raise HTTPException(status_code=400, detail="Текст реплики не может быть пустым")
        return ("segments",)

    m = edit_meeting(meeting_id, change)
    if m is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return m


@router.post("/api/meetings/{meeting_id}/segments/{segment_id}/merge")
def api_merge_segment(meeting_id: int, segment_id: int) -> Dict[str, Any]:
    from app.database import merge_segment_with_previous

    _ensure_not_transcribing(meeting_id)

    updated = merge_segment_with_previous(meeting_id, segment_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return updated

@router.post("/api/meetings/{meeting_id}/clean-fillers")
def api_clean_fillers(meeting_id: int) -> Dict[str, Any]:
    from app.transcriber import clean_meeting_transcript

    try:
        return clean_meeting_transcript(meeting_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/api/meetings/{meeting_id}/analytics")
def api_meeting_analytics(meeting_id: int) -> Dict[str, Any]:
    from app.analytics import conversation_analytics

    m = get_meeting(meeting_id)
    if not m:
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return conversation_analytics(m)

@router.get("/api/meetings/{meeting_id}/fillers")
def api_filler_stats(meeting_id: int) -> Dict[str, Any]:
    from app.database import filler_stats

    return filler_stats(meeting_id)

@router.post("/api/meetings/{meeting_id}/restore-segments")
def api_restore_segments(meeting_id: int) -> Dict[str, Any]:
    """Undoes the last filler cleanup (automatic or manual) for this recording."""
    from app.database import restore_segments_backup

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    if not restore_segments_backup(meeting_id):
        raise HTTPException(status_code=400, detail="Нет сохранённой версии для восстановления")
    return get_meeting(meeting_id)

# --- AI Summary & Custom Q&A via Ollama ---
@router.post("/api/meetings/{meeting_id}/remove-media")
def api_remove_media(meeting_id: int) -> Dict[str, Any]:
    """Deletes the sound of a recording, keeps the transcript and the analysis."""
    from app.database import remove_media

    _ensure_not_processing(meeting_id)
    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    remove_media(meeting_id)
    return get_meeting(meeting_id)

@router.delete("/api/meetings/{meeting_id}/summaries")
def api_delete_summary(meeting_id: int, key: str) -> Dict[str, Any]:
    """One analysis of a recording (a tab of Analysis: overview, tasks, chapters, a template...)."""
    from app.database import merge_summaries
    from app.obsidian import refresh_if_exported

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    merge_summaries(meeting_id, {}, remove=(key,))
    refresh_if_exported(meeting_id)
    return get_meeting(meeting_id)

@router.delete("/api/meetings/{meeting_id}/commitments")
def api_delete_meeting_commitments(meeting_id: int) -> Dict[str, Any]:
    from app import commitments

    return {"deleted": commitments.delete_for_meeting(meeting_id)}

@router.delete("/api/meetings/{meeting_id}/dubs/{lang}")
def api_delete_dub(meeting_id: int, lang: str) -> Dict[str, Any]:
    from app.database import delete_meeting_dub

    _ensure_not_processing(meeting_id)
    if not delete_meeting_dub(meeting_id, lang):
        raise HTTPException(status_code=404, detail="Дубляж не найден")
    return get_meeting(meeting_id)

@router.post("/api/meetings/{meeting_id}/clear-results")
def api_clear_results(meeting_id: int) -> Dict[str, Any]:
    """Deletes the transcript and the analysis of a recording, keeps its sound (to transcribe it again)."""
    from app.database import clear_results

    _ensure_not_processing(meeting_id)
    if not clear_results(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    return get_meeting(meeting_id)

@router.get("/api/privacy/cloud-log")
def api_cloud_log() -> List[Dict[str, Any]]:
    from app.llm import cloud_log

    return cloud_log()

@router.delete("/api/privacy/cloud-log")
def api_clear_cloud_log() -> Dict[str, Any]:
    from app.llm import clear_cloud_log

    clear_cloud_log()
    return {"ok": True}

class CommitmentExtractPayload(BaseModel):
    model: Optional[str] = None

@router.post("/api/meetings/{meeting_id}/commitments")
def api_extract_commitments(meeting_id: int, payload: CommitmentExtractPayload) -> List[Dict[str, Any]]:
    from app import commitments

    try:
        return commitments.extract(meeting_id, payload.model or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)[:300])

@router.get("/api/meetings/{meeting_id}/commitments")
def api_meeting_commitments(meeting_id: int) -> List[Dict[str, Any]]:
    from app import commitments

    return commitments.for_meetings([meeting_id])

@router.get("/api/commitments")
def api_commitments(series_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Open promises: of a series, or of all recordings."""
    from app import commitments

    return commitments.open_for_series(series_id) if series_id else commitments.all_open()

class CommitmentExportPayload(BaseModel):
    fmt: str = "md"  # md | todoist | csv
    meeting_id: Optional[int] = None  # one recording, or all open promises
    lang: str = "ru"

@router.post("/api/commitments/export")
def api_commitments_export(payload: CommitmentExportPayload) -> Dict[str, Any]:
    """Saves promises as tasks for another app (Save as dialog)."""
    from app import commitments
    from app.naming import base_name

    if payload.fmt not in ("md", "todoist", "csv"):
        raise HTTPException(status_code=400, detail="Неизвестный формат")
    items = commitments.for_meetings([payload.meeting_id]) if payload.meeting_id else commitments.all_open()
    if not items:
        raise HTTPException(status_code=404, detail="Нет обещаний для выгрузки")
    text = commitments.export_text(items, payload.fmt, payload.lang)
    m = get_meeting(payload.meeting_id) if payload.meeting_id else None
    ext = "md" if payload.fmt == "md" else "csv"
    base = (base_name(m) + " — ") if m else ""
    name = f"{base}{'tasks' if payload.lang == 'en' else 'задачи'}{'-todoist' if payload.fmt == 'todoist' else ''}.{ext}"
    chosen = _pick_export_path(m, name, ext)
    if not chosen:
        return {"ok": False, "cancelled": True}
    out = Path(chosen)
    # Excel and Todoist read a CSV with a BOM as UTF-8
    out.write_text(text, encoding="utf-8-sig" if ext == "csv" else "utf-8")
    if payload.meeting_id:
        _remember_export(payload.meeting_id, out)
    return {"ok": True, "path": str(out)}

class CommitmentStatusPayload(BaseModel):
    status: str

@router.patch("/api/commitments/{commitment_id}")
def api_commitment_status(commitment_id: int, payload: CommitmentStatusPayload) -> Dict[str, Any]:
    from app import commitments

    try:
        commitments.set_status(commitment_id, payload.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}

class NotesPayload(BaseModel):
    notes: str = ""

@router.patch("/api/meetings/{meeting_id}/notes")
def api_save_notes(meeting_id: int, payload: NotesPayload) -> Dict[str, Any]:
    """The user's own notes of a recording (written during the meeting or after it)."""
    from app.database import update_meeting_fields

    if not get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    update_meeting_fields(meeting_id, user_notes=payload.notes[:50000])
    return {"ok": True}

class TemplatesPayload(BaseModel):
    templates: List[Dict[str, Any]]

@router.get("/api/summary-templates")
def api_get_templates() -> List[Dict[str, Any]]:
    from app.database import summary_templates

    return summary_templates()

@router.put("/api/summary-templates")
def api_put_templates(payload: TemplatesPayload) -> List[Dict[str, Any]]:
    from app.database import save_summary_templates

    return save_summary_templates(payload.templates)

class SummaryRequestPayload(BaseModel):
    template: str = "protocol"
    custom_prompt: str = ""
    model: Optional[str] = None
    lang: Optional[str] = None

@router.post("/api/meetings/{meeting_id}/summary")
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

    template_prompt = ""
    if payload.template.startswith("tpl:"):
        from app.database import summary_templates

        tpl = next((x for x in summary_templates() if f"tpl:{x['id']}" == payload.template), None)
        if not tpl:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        template_prompt = tpl["prompt"]
    try:
        result_md = generate_meeting_summary(
            segments=segments,
            speakers=speakers,
            template_key=payload.template,
            custom_prompt=payload.custom_prompt,
            model_name=payload.model or None,
            answer_lang=payload.lang or m.get("language") or "ru",
            local_only=is_local_only(m),
            user_notes=m.get("user_notes") or "",
            template_prompt=template_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.database import merge_summaries
    from app.grounding import maybe_ground

    key = "custom" if payload.custom_prompt.strip() else payload.template
    result_md = maybe_ground(result_md, key, m)
    summaries = merge_summaries(meeting_id, {key: result_md}, remove=("error",))
    if key != "custom":
        from app.obsidian import refresh_if_exported

        refresh_if_exported(meeting_id)
    return {"key": key, "summary": result_md, "summaries": summaries}
