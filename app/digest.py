"""
A digest of several recordings: a folder (with its subfolders) or everything recorded in the last
days, as one Markdown summary of the topics, the decisions, who promised what by when, and the
questions left open, with [n] links to the recordings.

Each recording is given by its overview when it has one (short and already checked by the user),
otherwise by the beginning of its transcript. Recordings marked "local analysis only" never reach a
cloud model: they are left out, and a digest made only of them uses the local model.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.database import get_meeting, list_meetings, local_only_ids, meeting_ids_in_folder

MAX_MEETINGS = 40
PER_MEETING_CHARS = 2500
MIN_PER_MEETING_CHARS = 600
TRANSCRIPT_FALLBACK_CHARS = 3000


def _when(m: Dict[str, Any]) -> str:
    return str(m.get("recorded_at") or m.get("created_at") or "")[:19]


def digest_meetings(folder_id: Optional[int] = None, days: Optional[int] = None) -> List[Dict[str, Any]]:
    """Completed recordings in scope, oldest first (a digest reads like a timeline)."""
    rows = [m for m in list_meetings() if m.get("status") == "completed" and m.get("has_transcript")]
    if folder_id is not None:
        ids = set(meeting_ids_in_folder(folder_id))
        rows = [m for m in rows if m["id"] in ids]
    if days:
        since = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
        rows = [m for m in rows if _when(m) >= since]
    rows.sort(key=_when)
    return rows[-MAX_MEETINGS:]


def _material(m: Dict[str, Any]) -> str:
    from app.ollama_client import build_transcript_lines

    summ = m.get("summaries") or {}
    text = (summ.get("protocol") or summ.get("tldr") or "").strip()
    if text:
        return "Обзор записи:\n" + text[:PER_MEETING_CHARS]
    lines = "\n".join(build_transcript_lines(m.get("segments") or [], m.get("speakers") or {}))
    return "Начало расшифровки:\n" + lines[:TRANSCRIPT_FALLBACK_CHARS]


def make_digest(folder_id: Optional[int] = None, days: Optional[int] = None, answer_lang: str = "ru",
                model_name: Optional[str] = None) -> Dict[str, Any]:
    from app.llm import LlmError, target
    from app.ollama_client import _lang_phrase

    rows = digest_meetings(folder_id, days)
    if not rows:
        raise ValueError("Нет расшифрованных записей за выбранный период")
    try:
        llm = target(model_name)
        excluded = 0
        if llm.is_cloud:
            private = local_only_ids()
            kept = [m for m in rows if m["id"] not in private]
            excluded = len(rows) - len(kept)
            if not kept:
                llm, excluded = target(None if model_name == "cloud" else model_name, local_only=True), 0
            else:
                rows = kept
    except LlmError as e:
        raise RuntimeError(str(e)) from e

    meetings = [m for m in (get_meeting(r["id"]) for r in rows) if m]
    budget = llm.max_chars - 3000
    # Every recording gets an equal share of what the model can read; below a useful size the
    # oldest recordings are left out instead
    while len(meetings) > 1 and budget // len(meetings) < MIN_PER_MEETING_CHARS:
        meetings.pop(0)
    per = min(PER_MEETING_CHARS, budget // max(1, len(meetings)))
    blocks = [f"[{n}] «{m.get('title') or ''}», {_when(m)[:16]}\n{_material(m)[:per]}" for n, m in enumerate(meetings, 1)]
    lang = _lang_phrase(answer_lang)
    system = (
        "Ты составляешь дайджест по нескольким записям встреч и звонков. Используй ТОЛЬКО приведённые материалы. "
        "Структура в Markdown: «## Главное» (3–6 пунктов), «## Решения», «## Задачи» (кто — что — срок, если сказано), "
        "«## Открытые вопросы», «## По записям» (одна строка на запись). "
        "После каждого утверждения ставь ссылку на запись в квадратных скобках, например [2] или [1][3]. "
        "Пропускай разделы, для которых нет материала. Без эмодзи и блоков кода. "
        f"Пиши {lang}."
    )
    answer = llm.chat(system, "Материалы записей:\n\n" + "\n\n".join(blocks) + "\n\nСоставь дайджест.")
    sources = [{"n": n, "meeting_id": int(m["id"]), "title": m.get("title") or "", "date": _when(m)[:10],
                "start": 0.0, "speaker": "", "text": ""} for n, m in enumerate(meetings, 1)]
    return {"answer": answer, "sources": sources, "found": True, "model": llm.label, "cloud": llm.is_cloud,
            "mode": "digest", "count": len(meetings), "excluded_local": excluded}
