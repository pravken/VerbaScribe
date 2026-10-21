"""
Commitments: who promised what to whom, by when — across the meetings of a series.

The analysis model reads a transcript and lists the promises made in it as JSON lines; each one is
checked against the transcript (app/grounding.py) and stored with its time stamp. They stay open
until ticked off (or a later meeting says they are done), and the open ones of a series go into
"since last time", the brief before the next meeting and the questions during a call.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.database import _db_lock, get_connection, get_meeting

_SYSTEM = (
    "Ты извлекаешь из расшифровки встречи обещания и поручения: кто взялся что-то сделать, для кого и к какому сроку. "
    "Только то, что прямо сказано в тексте; не придумывай. Ответ — только строки JSON, по одному объекту на строку, "
    'без пояснений: {"who": "кто сделает", "to": "для кого (или пусто)", "what": "что сделать, коротко", '
    '"due": "срок словами из текста или пусто", "time": "ММ:СС места в записи"}. '
    "Имена — как в расшифровке. Если обещаний нет, ответь пустой строкой."
)


def _init() -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                who TEXT NOT NULL DEFAULT '',
                to_whom TEXT NOT NULL DEFAULT '',
                what TEXT NOT NULL,
                due TEXT NOT NULL DEFAULT '',
                at REAL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                done_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS commitments_meeting ON commitments (meeting_id)")
        conn.commit()
        conn.close()


def _seconds(text: str) -> Optional[float]:
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text or "")
    if not m:
        return None
    a, b, c = m.groups()
    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)


def parse_answer(answer: str) -> List[Dict[str, Any]]:
    """JSON objects of the model's answer (one per line; a list or code fences are tolerated)."""
    text = re.sub(r"```(?:json)?", "", answer or "")
    found: List[Dict[str, Any]] = []
    candidates = re.findall(r"\{[^{}]*\}", text)
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        what = str(obj.get("what") or "").strip()
        if not what:
            continue
        found.append({
            "who": str(obj.get("who") or "").strip()[:80],
            "to": str(obj.get("to") or "").strip()[:80],
            "what": what[:300],
            "due": str(obj.get("due") or "").strip()[:80],
            "at": _seconds(str(obj.get("time") or "")),
        })
    return found


def supported(item: Dict[str, Any], m: Dict[str, Any]) -> bool:
    """The promise is in the transcript (not made up by the model)."""
    from app.grounding import UNVERIFIED_MARK, ground_summary

    ts = f" [{int(item['at'] // 60):02d}:{int(item['at'] % 60):02d}]" if item.get("at") is not None else ""
    line = f"- {item['who']} {item['what']}{ts}"
    return UNVERIFIED_MARK not in ground_summary(line, m.get("segments") or [], m.get("speakers") or {})


def extract(meeting_id: int, model_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Finds the promises of a recording (again: the earlier list of this recording is replaced,
    keeping what was ticked off)."""
    from app.database import is_local_only
    from app.llm import target
    from app.ollama_client import build_transcript_lines

    m = get_meeting(meeting_id)
    if not m or not m.get("segments"):
        raise ValueError("Нет текста записи")
    llm = target(model_name, local_only=is_local_only(m))
    text = "\n".join(build_transcript_lines(m["segments"], m.get("speakers") or {}))[: max(8000, llm.max_chars - 3000)]
    items = [x for x in parse_answer(llm.chat(_SYSTEM, "Расшифровка:\n" + text)) if supported(x, m)]
    _init()
    now = datetime.now().isoformat(timespec="seconds")
    with _db_lock:
        conn = get_connection()
        done = {r["what"].casefold() for r in conn.execute(
            "SELECT what FROM commitments WHERE meeting_id = ? AND status = 'done'", (meeting_id,)).fetchall()}
        conn.execute("DELETE FROM commitments WHERE meeting_id = ?", (meeting_id,))
        for x in items:
            status = "done" if x["what"].casefold() in done else "open"
            conn.execute(
                "INSERT INTO commitments (meeting_id, who, to_whom, what, due, at, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (meeting_id, x["who"], x["to"], x["what"], x["due"], x["at"], status, now))
        conn.commit()
        conn.close()
    return for_meetings([meeting_id])


def _rows(sql: str, args: tuple) -> List[Dict[str, Any]]:
    _init()
    with _db_lock:
        conn = get_connection()
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.close()
    return rows


def for_meetings(ids: List[int], status: Optional[str] = None) -> List[Dict[str, Any]]:
    if not ids:
        return []
    q = f"SELECT * FROM commitments WHERE meeting_id IN ({','.join('?' * len(ids))})"
    args: tuple = tuple(int(i) for i in ids)
    if status:
        q += " AND status = ?"
        args += (status,)
    rows = _rows(q + " ORDER BY meeting_id, at", args)
    titles = {}
    for r in rows:
        if r["meeting_id"] not in titles:
            mm = get_meeting(r["meeting_id"]) or {}
            titles[r["meeting_id"]] = (mm.get("title") or "", str(mm.get("recorded_at") or mm.get("created_at") or "")[:10])
        r["meeting_title"], r["meeting_date"] = titles[r["meeting_id"]]
    return rows


def open_for_series(series_id: int) -> List[Dict[str, Any]]:
    from app.series import series_meetings

    return for_meetings([r["id"] for r in series_meetings(series_id)], status="open")


def all_open() -> List[Dict[str, Any]]:
    ids = [r["meeting_id"] for r in _rows("SELECT DISTINCT meeting_id FROM commitments WHERE status = 'open'", ())]
    return for_meetings(ids, status="open")


def set_status(commitment_id: int, status: str) -> None:
    if status not in ("open", "done"):
        raise ValueError("Неизвестное состояние")
    _init()
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE commitments SET status = ?, done_at = ? WHERE id = ?",
                     (status, datetime.now().isoformat(timespec="seconds") if status == "done" else "", commitment_id))
        conn.commit()
        conn.close()


def as_text(items: List[Dict[str, Any]]) -> str:
    """For prompts: '- Анна → клиенту: презентация (к пятнице) — «Встреча», 2026-10-14'."""
    out = []
    for x in items:
        who = x["who"] + (f" → {x['to_whom']}" if x.get("to_whom") else "")
        due = f" ({x['due']})" if x.get("due") else ""
        out.append(f"- {who}: {x['what']}{due} — «{x.get('meeting_title', '')}», {x.get('meeting_date', '')}")
    return "\n".join(out)
