"""The home page: the latest recordings with a line of their overview, the meetings of the working
day from the calendar, the dictation button."""

import json
import re
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.database import get_connection, list_meetings

router = APIRouter()

_MD_RE = re.compile(r"[#*_`>\[\]]|^\s*[-•]\s*", re.M)


def _first_line(summaries: Dict[str, Any]) -> str:
    """The first sentence of the short summary (or of the protocol), plain text, for one line."""
    for key in ("tldr", "protocol"):
        text = summaries.get(key)
        if isinstance(text, str) and text.strip():
            for line in text.splitlines():
                if line.lstrip().startswith("#"):  # a heading ("Итоги"), not what was said
                    continue
                line = _MD_RE.sub("", line).strip()
                if len(line) > 3:
                    return line[:200]
    return ""


@router.get("/api/home/recent")
def api_home_recent(limit: int = 8) -> List[Dict[str, Any]]:
    """The latest recordings with a line of their overview and their open tasks."""
    from app import commitments

    limit = max(1, min(30, limit))
    rows = sorted(list_meetings(), key=lambda m: str(m.get("recorded_at") or m.get("created_at") or ""), reverse=True)[:limit]
    ids = [m["id"] for m in rows]
    lines: Dict[int, str] = {}
    if ids:
        conn = get_connection()
        try:
            marks = ",".join("?" * len(ids))
            for r in conn.execute(f"SELECT id, summaries_json FROM meetings WHERE id IN ({marks})", ids).fetchall():
                try:
                    lines[r["id"]] = _first_line(json.loads(r["summaries_json"] or "{}"))
                except ValueError:
                    lines[r["id"]] = ""
        finally:
            conn.close()
    open_tasks: Dict[int, int] = {}
    for c in commitments.for_meetings(ids, status="open") if ids else []:
        open_tasks[c["meeting_id"]] = open_tasks.get(c["meeting_id"], 0) + 1
    return [{"id": m["id"], "title": m.get("title") or "", "status": m.get("status"), "progress": m.get("progress"),
             "duration": m.get("duration") or 0, "is_video": m.get("is_video"), "source": m.get("source"),
             "recorded_at": m.get("recorded_at") or m.get("created_at") or "", "summary": lines.get(m["id"], ""),
             "tasks": open_tasks.get(m["id"], 0)} for m in rows]


def _parse_local(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Нужна дата и время вида 2026-10-24T09:00")


@router.get("/api/calendar/day")
def api_calendar_day(start: str, end: str, refresh: bool = False) -> Dict[str, Any]:
    """The meetings of the working day (start…end, local time; past midnight for a night shift), each
    with the recording made during it, if any."""
    from app import calendar_ics
    from app.database import get_all_settings

    a, b = _parse_local(start), _parse_local(end)
    if not a < b or (b - a).days > 2:
        raise HTTPException(status_code=400, detail="Неверный промежуток времени")
    if refresh:
        calendar_ics.events(force=True)
    items = calendar_ics.day(a, b)
    made = []
    for m in list_meetings():
        at = str(m.get("recorded_at") or "")
        try:
            t = datetime.fromisoformat(at.replace("Z", "")) if at else None
        except ValueError:
            t = None
        if t and a <= t <= b:
            made.append((t, m))
    for it in items:
        s, e = datetime.fromisoformat(it["start"]), datetime.fromisoformat(it["end"])
        hit = next((m for t, m in made if s.replace(second=0) <= t.replace(tzinfo=None) < e), None)
        it["recording"] = {"id": hit["id"], "status": hit.get("status"), "progress": hit.get("progress")} if hit else None
    return {"configured": bool((get_all_settings().get("calendar_ics") or "").strip()), "events": items,
            **calendar_ics.status()}


# --- Dictation from the home page: the text is shown, not typed (app/dictation.py) ---
@router.get("/api/dictation")
def api_dictation_status() -> Dict[str, Any]:
    from app import dictation

    st = dictation.status()
    return {k: st.get(k) for k in ("active", "busy", "error", "last_text", "started_at")}


@router.post("/api/dictation/toggle")
def api_dictation_toggle() -> Dict[str, Any]:
    from app import dictation

    return {"state": dictation.toggle(type_out=False)}


@router.post("/api/dictation/cancel")
def api_dictation_cancel() -> Dict[str, Any]:
    from app import dictation

    dictation.cancel()
    return {"ok": True}
