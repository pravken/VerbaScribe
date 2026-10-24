"""
Commitments: who promised what to whom, by when — across the meetings of a series.

The analysis model reads a transcript and lists the promises made in it as JSON lines; each one is
checked against the transcript (app/grounding.py) and stored with its time stamp. They stay open
until ticked off (or a later meeting says they are done), and the open ones of a series go into
"since last time", the brief before the next meeting and the questions during a call.

They are also the tasks of the Tasks page: who a task is on is a kind of assignee as well as a name
(someone who speaks in the recording, someone only named in it, a department, another company, or
nobody), with the words of the recording it came from; the due date is a date (from the words of the
recording, said on the day of the meeting), and tasks can be added, reassigned and moved by hand.
"""

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.database import _db_lock, get_connection, get_meeting

_SYSTEM = (
    "Ты извлекаешь из расшифровки встречи обещания и поручения: кто взялся что-то сделать, для кого и к какому сроку. "
    "Только то, что прямо сказано в тексте; не придумывай. Ответ — только строки JSON, по одному объекту на строку, "
    'без пояснений: {"who": "кто сделает", "kind": "кто это", "to": "для кого (или пусто)", "what": "что сделать, коротко", '
    '"due": "срок словами из текста или пусто", "time": "ММ:СС места в записи", '
    '"quote": "дословные слова из текста, где это сказано"}. '
    "kind — одно из: part (говорит в записи), ext (человек, которого только назвали), team (отдел или группа), "
    "org (другая компания), none (исполнитель не назван, тогда who пусто). "
    "Имена — как в расшифровке. Если обещаний нет, ответь пустой строкой."
)

# Who a task is on: someone who speaks in the recording, someone only named in it, a department or
# a group, another company, nobody
KINDS = ("part", "ext", "team", "org", "none")
_KIND_WORDS = {"participant": "part", "speaker": "part", "outside": "ext", "mentioned": "ext", "person": "ext",
               "department": "team", "group": "team", "company": "org", "external": "org"}


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
        # The tasks of the Tasks page (added later: an older library gets the columns empty).
        # meeting_id 0 is a task added by hand without a recording.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(commitments)").fetchall()}
        for column, ddl in (("who_kind", "TEXT NOT NULL DEFAULT ''"), ("quote", "TEXT NOT NULL DEFAULT ''"),
                            ("due_date", "TEXT NOT NULL DEFAULT ''"), ("source", "TEXT NOT NULL DEFAULT 'ai'"),
                            ("edited", "INTEGER NOT NULL DEFAULT 0"), ("updated_at", "TEXT NOT NULL DEFAULT ''")):
            if column not in have:
                conn.execute(f"ALTER TABLE commitments ADD COLUMN {column} {ddl}")
        conn.commit()
        conn.close()


def norm_kind(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in KINDS else _KIND_WORDS.get(v, "ext")


# --- Due words → a date, said on the day of the meeting: "к пятнице", "до 15 ноября", "завтра" ---
_WEEKDAYS = (("понедельник", "monday"), ("вторник", "tuesday"), ("сред", "wednesday"), ("четверг", "thursday"),
             ("пятниц", "friday"), ("суббот", "saturday"), ("воскресень", "sunday"))
_MONTHS = (("январ", "jan"), ("феврал", "feb"), ("март", "mar"), ("апрел", "apr"), ("ма", "may"), ("июн", "jun"),
           ("июл", "jul"), ("август", "aug"), ("сентябр", "sep"), ("октябр", "oct"), ("ноябр", "nov"), ("декабр", "dec"))
_COUNTS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
           "десять": 10, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}


def _month_of(word: str) -> Optional[int]:
    w = word.lower()
    if w in ("мая", "май"):
        return 5
    for i, (ru, en) in enumerate(_MONTHS, 1):
        if (ru != "ма" and w.startswith(ru)) or w.startswith(en):
            return i
    return None


def _near_year(base: date, month: int, day: int) -> Optional[date]:
    """A day and month without a year: this year, or the next one when it is well past (said in
    December about January)."""
    try:
        d = date(base.year, month, day)
    except ValueError:
        return None
    if (base - d).days > 60:
        try:
            d = date(base.year + 1, month, day)
        except ValueError:
            return None
    return d


def parse_due(text: str, base: date) -> Optional[date]:
    """The day the due words mean, said on `base`; None when they name no day."""
    s = (text or "").lower().replace("ё", "е")
    if not s.strip():
        return None
    if "послезавтра" in s or "day after tomorrow" in s:
        return base + timedelta(days=2)
    if "завтра" in s or "tomorrow" in s:
        return base + timedelta(days=1)
    if "сегодня" in s or "today" in s or "tonight" in s:
        return base
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if y:
            try:
                return date(int(y) + (2000 if len(y) == 2 else 0), mo, d)
            except ValueError:
                return None
        return _near_year(base, mo, d)
    m = re.search(r"\b(\d{1,2})\s+([а-яa-z]+)", s)
    if m and _month_of(m.group(2)):
        return _near_year(base, _month_of(m.group(2)), int(m.group(1)))
    m = re.search(r"\b([a-z]{3,})\s+(\d{1,2})\b", s)
    if m and _month_of(m.group(1)):
        return _near_year(base, _month_of(m.group(1)), int(m.group(2)))
    m = re.search(r"через\s+(\d+|[а-я]+)\s+(дн|день|недел|месяц)", s) or re.search(r"in\s+(\d+|[a-z]+)\s+(day|week|month)", s)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _COUNTS.get(m.group(1), 0)
        if n:
            unit = m.group(2)
            return base + timedelta(days=n * (7 if unit.startswith(("недел", "week")) else 30 if unit.startswith(("месяц", "month")) else 1))
    if re.search(r"через\s+неделю|in a week", s):
        return base + timedelta(days=7)
    if re.search(r"на следующей неделе|next week", s):
        return base + timedelta(days=7 - base.weekday())  # its Monday
    if re.search(r"через\s+месяц|in a month", s):
        return base + timedelta(days=30)
    if re.search(r"конц\w*\s+месяц|end of (the )?month", s):
        return date(base.year + (base.month == 12), base.month % 12 + 1, 1) - timedelta(days=1)
    if re.search(r"конц\w*\s+недел|на этой неделе|end of (the )?week|this week", s):
        return base + timedelta(days=max(0, 4 - base.weekday()))  # its Friday
    for i, (ru, en) in enumerate(_WEEKDAYS):
        if ru in s or en in s:
            return base + timedelta(days=(i - base.weekday()) % 7 or 7)
    return None


def _meeting_day(m: Optional[Dict[str, Any]]) -> date:
    raw = str((m or {}).get("recorded_at") or (m or {}).get("created_at") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def participants(m: Optional[Dict[str, Any]]) -> List[str]:
    """The names of the people who speak in a recording."""
    out: List[str] = []
    for v in ((m or {}).get("speakers") or {}).values():
        name = str((v.get("name") if isinstance(v, dict) else v) or "").strip()
        if name and name not in out:
            out.append(name)
    return out


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
        who = str(obj.get("who") or "").strip()[:80]
        found.append({
            "who": who,
            "kind": norm_kind(obj.get("kind")) if who else "none",
            "to": str(obj.get("to") or "").strip()[:80],
            "what": what[:300],
            "due": str(obj.get("due") or "").strip()[:80],
            "at": _seconds(str(obj.get("time") or "")),
            "quote": str(obj.get("quote") or "").strip().strip("«»\"")[:300],
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
    day, speaking = _meeting_day(m), {n.casefold() for n in participants(m)}
    with _db_lock:
        conn = get_connection()
        done = {r["what"].casefold() for r in conn.execute(
            "SELECT what FROM commitments WHERE meeting_id = ? AND status = 'done'", (meeting_id,)).fetchall()}
        # What the user added or changed by hand stays as they left it
        kept = {r["what"].casefold() for r in conn.execute(
            "SELECT what FROM commitments WHERE meeting_id = ? AND (source = 'manual' OR edited = 1)", (meeting_id,)).fetchall()}
        conn.execute("DELETE FROM commitments WHERE meeting_id = ? AND source != 'manual' AND edited = 0", (meeting_id,))
        for x in items:
            if x["what"].casefold() in kept:
                continue
            status = "done" if x["what"].casefold() in done else "open"
            kind = "part" if x["who"].casefold() in speaking else x["kind"]
            due_date = parse_due(x["due"], day)
            conn.execute(
                "INSERT INTO commitments (meeting_id, who, to_whom, what, due, at, status, created_at, who_kind, quote,"
                " due_date, source) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'ai')",
                (meeting_id, x["who"], x["to"], x["what"], x["due"], x["at"], status, now, kind, x["quote"],
                 due_date.isoformat() if due_date else ""))
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
    return _enrich(_rows(q + " ORDER BY meeting_id, at", args))


def _enrich(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The recording's title and date, and for older rows the kind and the date their words give."""
    meetings: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        mid = r["meeting_id"]
        if mid not in meetings:
            meetings[mid] = (get_meeting(mid) or {}) if mid else {}
        mm = meetings[mid]
        r["meeting_title"] = mm.get("title") or ""
        r["meeting_date"] = str(mm.get("recorded_at") or mm.get("created_at") or "")[:10]
        if not r.get("who_kind"):
            names = {n.casefold() for n in participants(mm)}
            r["who_kind"] = "none" if not r["who"] else "part" if r["who"].casefold() in names else "ext"
        if not r.get("due_date") and r.get("due"):
            d = parse_due(r["due"], _meeting_day(mm) if mm else date.today())
            r["due_date"] = d.isoformat() if d else ""
        r.setdefault("due_date", "")
        r.setdefault("quote", "")
        r.setdefault("source", "ai")
    return rows


def open_for_series(series_id: int) -> List[Dict[str, Any]]:
    from app.series import series_meetings

    return for_meetings([r["id"] for r in series_meetings(series_id)], status="open")


def all_open() -> List[Dict[str, Any]]:
    ids = [r["meeting_id"] for r in _rows("SELECT DISTINCT meeting_id FROM commitments WHERE status = 'open'", ())]
    return for_meetings(ids, status="open")


def all_tasks() -> List[Dict[str, Any]]:
    """Every task of the library, open and done, the ones without a recording included."""
    return _enrich(_rows("SELECT * FROM commitments ORDER BY meeting_id, at", ()))


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    rows = _enrich(_rows("SELECT * FROM commitments WHERE id = ?", (int(task_id),)))
    return rows[0] if rows else None


def _clean_date(value: Any) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise ValueError("Неверная дата")


def create(what: str, who: str = "", who_kind: str = "", due_date: str = "", meeting_id: int = 0,
           at: Optional[float] = None) -> Dict[str, Any]:
    """A task added by hand, with or without a recording (and a moment of it)."""
    what = (what or "").strip()[:300]
    if not what:
        raise ValueError("Пустая задача")
    if meeting_id and not get_meeting(int(meeting_id)):
        raise ValueError("Запись не найдена")
    who = (who or "").strip()[:80]
    kind = norm_kind(who_kind) if who else "none"
    due = _clean_date(due_date)
    _init()
    now = datetime.now().isoformat(timespec="seconds")
    with _db_lock:
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO commitments (meeting_id, who, to_whom, what, due, at, status, created_at, who_kind, due_date,"
            " source, updated_at) VALUES (?,?, '', ?, '', ?, 'open', ?,?,?, 'manual', ?)",
            (int(meeting_id or 0), who, what, at if meeting_id else None, now, kind, due, now))
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
    return get_task(new_id) or {}


_EDITABLE = ("who", "who_kind", "due_date", "what", "status")


def update(task_id: int, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Reassigns, moves, renames or ticks off a task. A change by hand is kept when the tasks of the
    recording are found again."""
    fields = {k: v for k, v in changes.items() if k in _EDITABLE and v is not None}
    if not fields:
        raise ValueError("Нечего менять")
    if "status" in fields and fields["status"] not in ("open", "done"):
        raise ValueError("Неизвестное состояние")
    if "what" in fields:
        fields["what"] = str(fields["what"]).strip()[:300]
        if not fields["what"]:
            raise ValueError("Пустая задача")
    if "who" in fields:
        fields["who"] = str(fields["who"]).strip()[:80]
        if not fields["who"]:
            fields["who_kind"] = "none"
    if "who_kind" in fields:
        fields["who_kind"] = norm_kind(fields["who_kind"])
    if "due_date" in fields:
        fields["due_date"] = _clean_date(fields["due_date"])
        fields["due"] = ""  # the date is the user's now, not the words of the recording
    now = datetime.now().isoformat(timespec="seconds")
    if "status" in fields:
        fields["done_at"] = now if fields["status"] == "done" else ""
    # ticking off is not a change of the task itself
    edited = any(k in fields for k in ("who", "who_kind", "due_date", "what"))
    _init()
    with _db_lock:
        conn = get_connection()
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?" + (", edited = 1" if edited else "")
        n = conn.execute(f"UPDATE commitments SET {sets} WHERE id = ?", (*fields.values(), now, int(task_id))).rowcount
        conn.commit()
        conn.close()
    if not n:
        raise LookupError("Задача не найдена")
    return get_task(task_id) or {}


def delete(task_id: int) -> bool:
    _init()
    with _db_lock:
        conn = get_connection()
        n = conn.execute("DELETE FROM commitments WHERE id = ?", (int(task_id),)).rowcount
        conn.commit()
        conn.close()
    return bool(n)


def people(tasks: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Everyone tasks can be on: the people who speak in the recordings that have tasks and the
    names the tasks carry (with their kind), each with its count of open and done tasks."""
    tasks = all_tasks() if tasks is None else tasks
    found: Dict[str, Dict[str, Any]] = {}

    def add(name: str, kind: str) -> Dict[str, Any]:
        key = name.casefold()
        if key not in found:
            found[key] = {"name": name, "kind": kind, "open": 0, "done": 0}
        elif kind == "part":  # someone who speaks somewhere is a participant
            found[key]["kind"] = "part"
        return found[key]

    for mid in sorted({t["meeting_id"] for t in tasks if t["meeting_id"]}):
        for name in participants(get_meeting(mid)):
            add(name, "part")
    for t in tasks:
        if t["who"]:
            p = add(t["who"], t["who_kind"] or "ext")
            p["done" if t["status"] == "done" else "open"] += 1
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(found.values(), key=lambda p: (order.get(p["kind"], 9), -p["open"], p["name"].casefold()))


def guess(text: str, names: List[str], base: Optional[date] = None) -> Dict[str, Any]:
    """Who and when the words of a new task name («Анне подготовить отчёт к пятнице»)."""
    s = (text or "").casefold()
    who = ""
    for name in sorted(names, key=len, reverse=True):
        first = name.casefold().split()[0] if name.strip() else ""
        # a stem catches the other forms of a name (Анна, Анне, Анну)
        stem = first[:max(3, len(first) - 2)] if len(first) > 4 else first[:max(3, len(first) - 1)]
        if len(stem) >= 3 and re.search(r"(?:^|\W)" + re.escape(stem), s):
            who = name
            break
    d = parse_due(text, base or date.today())
    return {"who": who, "due_date": d.isoformat() if d else ""}


_REPORT_SYSTEM = (
    "Ты пишешь короткую сводку по задачам команды. Перескажи факты ниже связным деловым текстом на языке фактов, "
    "по тем же разделам. Ничего не добавляй и не выдумывай: только эти задачи, люди и сроки. Без вступлений и "
    "без markdown-разметки, абзацы — через пустую строку."
)


def report(tasks: List[Dict[str, Any]], start: str, end: str, include: List[str], fmt: str = "short",
           lang: str = "ru", today: Optional[date] = None) -> Dict[str, Any]:
    """The summary of a period: the tasks due in it, done / open / overdue, as lines of text (the
    Summary panel). fmt: short, people (one line a person), mail (a letter to the team)."""
    today = today or date.today()
    ru = lang != "en"
    lo, hi = _clean_date(start), _clean_date(end)
    scope = [t for t in tasks if t.get("due_date") and lo <= t["due_date"] <= hi]
    over = [t for t in scope if t["status"] != "done" and t["due_date"] < today.isoformat()]
    done = [t for t in scope if t["status"] == "done"]
    open_ = [t for t in scope if t["status"] != "done" and t not in over]
    nobody = "без исполнителя" if ru else "no assignee"

    def short(t: Dict[str, Any]) -> str:
        w = t["what"]
        return "«" + (w if len(w) <= 60 else w[:58] + "…") + "»" if ru else '"' + (w if len(w) <= 60 else w[:58] + "…") + '"'

    def who(t: Dict[str, Any]) -> str:
        return t["who"] or nobody

    def day(t: Dict[str, Any]) -> str:
        d = date.fromisoformat(t["due_date"])
        return f"{d.day:02d}.{d.month:02d}"

    lines: List[Dict[str, str]] = []
    if fmt == "people":
        names = sorted({who(t) for t in scope}, key=lambda n: (-sum(who(t) == n for t in scope), n.casefold()))
        for n in names:
            mine = [t for t in scope if who(t) == n]
            d, o = [t for t in mine if t in done], [t for t in mine if t in over]
            w = [t for t in mine if t in open_]
            bits = [(f"{len(d)} из {len(mine)} выполнено" if ru else f"{len(d)} of {len(mine)} done")]
            if "over" in include and o:
                bits.append(("просрочено: " if ru else "overdue: ") + ", ".join(map(short, o)))
            if "open" in include and w:
                bits.append(("в работе: " if ru else "in progress: ") + ", ".join(map(short, w)))
            if "done" in include and d:
                bits.append(("сделано: " if ru else "done: ") + ", ".join(map(short, d)))
            lines.append({"text": f"{n} — " + "; ".join(bits) + ".", "tone": "over" if o else "done" if len(d) == len(mine) else ""})
    elif fmt == "mail":
        lines.append({"text": (f"Коллеги, итоги по задачам за {_period_text(lo, hi, ru)}." if ru
                               else f"Hello all, here is where our tasks stand for {_period_text(lo, hi, ru)}."), "tone": ""})
        if "done" in include:
            lines.append({"text": (f"Сделано ({len(done)}): " if ru else f"Done ({len(done)}): ")
                          + ("; ".join(t["what"] for t in done) or "—") + ".", "tone": ""})
        if "open" in include:
            lines.append({"text": (f"В работе ({len(open_)}): " if ru else f"In progress ({len(open_)}): ")
                          + ("; ".join(f"{t['what']} — {who(t)}, {day(t)}" for t in open_) or "—") + ".", "tone": ""})
        if "over" in include:
            lines.append({"text": (f"Просрочено ({len(over)}): " if ru else f"Overdue ({len(over)}): ")
                          + ("; ".join(f"{t['what']} — {who(t)}, {day(t)}" for t in over) or "—") + ".", "tone": ""})
    else:
        lines.append({"text": (f"Закрыто {len(done)} из {len(scope)}, в работе {len(open_)}, просрочено {len(over)}." if ru
                               else f"{len(done)} of {len(scope)} closed, {len(open_)} in progress, {len(over)} overdue."), "tone": ""})
        if "over" in include and over:
            lines.append({"text": ("Просрочено: " if ru else "Overdue: ") + "; ".join(f"{short(t)} — {who(t)}" for t in over) + ".", "tone": "over"})
        if "done" in include and done:
            lines.append({"text": ("Сделано: " if ru else "Done: ") + ", ".join(map(short, done)) + ".", "tone": "done"})
        if "open" in include and open_:
            nearest = sorted(open_, key=lambda t: t["due_date"])[:3]
            lines.append({"text": ("Ближайшие: " if ru else "Next: ") + ", ".join(f"{short(t)} ({day(t)})" for t in nearest) + ".", "tone": ""})
    title = ("Сводка за " if ru else "Summary for ") + _period_text(lo, hi, ru)
    return {"title": title, "lines": lines, "text": title + "\n\n" + "\n\n".join(x["text"] for x in lines),
            "counts": {"all": len(scope), "done": len(done), "open": len(open_), "over": len(over)}}


def _period_text(lo: str, hi: str, ru: bool) -> str:
    a, b = date.fromisoformat(lo), date.fromisoformat(hi)
    gen = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
    if not ru:
        return f"{a.strftime('%b')} {a.day} – {b.strftime('%b')} {b.day}"
    if a.month == b.month and a.year == b.year:
        return f"{a.day}–{b.day} {gen[b.month - 1]}"
    return f"{a.day} {gen[a.month - 1]} – {b.day} {gen[b.month - 1]}"


def report_by_model(facts: Dict[str, Any], local_only: bool = False, model_name: Optional[str] = None) -> Dict[str, Any]:
    """The same summary told by the analysis model from its facts (only them: the facts go in, the
    model writes the sentences)."""
    from app.llm import target

    llm = target(model_name, local_only=local_only)
    text = llm.chat(_REPORT_SYSTEM, facts["text"]).strip()
    if not text:
        return facts
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return {**facts, "lines": [{"text": p, "tone": ""} for p in paragraphs], "text": facts["title"] + "\n\n" + "\n\n".join(paragraphs),
            "model": llm.label, "cloud": llm.is_cloud}


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


def delete_for_meeting(meeting_id: int) -> int:
    """The promises found in one recording (the "Clear" button of its Promises tab)."""
    _init()
    with _db_lock:
        conn = get_connection()
        n = conn.execute("DELETE FROM commitments WHERE meeting_id = ?", (meeting_id,)).rowcount
        conn.commit()
        conn.close()
    return n


def as_text(items: List[Dict[str, Any]]) -> str:
    """For prompts: '- Анна → клиенту: презентация (к пятнице) — «Встреча», 2026-10-14'."""
    out = []
    for x in items:
        who = x["who"] + (f" → {x['to_whom']}" if x.get("to_whom") else "")
        due = f" ({x['due']})" if x.get("due") else ""
        out.append(f"- {who}: {x['what']}{due} — «{x.get('meeting_title', '')}», {x.get('meeting_date', '')}")
    return "\n".join(out)


def export_text(items: List[Dict[str, Any]], fmt: str, lang: str = "ru") -> str:
    """md: Markdown tasks (Obsidian, Notion, any notes app); todoist: Todoist's CSV import;
    csv: a table (Notion database, Excel)."""
    import csv
    import io

    if fmt == "md":
        lines = []
        for x in items:
            box = "[x]" if x.get("status") == "done" else "[ ]"
            who = x["who"] + (f" → {x['to_whom']}" if x.get("to_whom") else "")
            due = f" 📅 {x['due']}" if x.get("due") else ""
            lines.append(f"- {box} {who}: {x['what']}{due} — {x.get('meeting_title', '')} ({x.get('meeting_date', '')})")
        return "\n".join(lines) + "\n"
    buf = io.StringIO()
    w = csv.writer(buf)
    if fmt == "todoist":
        w.writerow(["TYPE", "CONTENT", "DESCRIPTION", "PRIORITY", "INDENT", "AUTHOR", "RESPONSIBLE", "DATE", "DATE_LANG", "TIMEZONE"])
        for x in items:
            w.writerow(["task", f"{x['what']} ({x['who']})", f"{x.get('meeting_title', '')}, {x.get('meeting_date', '')}",
                        4, 1, "", "", x.get("due") or "", "ru" if lang == "ru" else "en", ""])
    else:
        w.writerow(["Задача" if lang == "ru" else "Task", "Кто" if lang == "ru" else "Who", "Кому" if lang == "ru" else "For",
                    "Срок" if lang == "ru" else "Due", "Встреча" if lang == "ru" else "Meeting",
                    "Дата" if lang == "ru" else "Date", "Выполнено" if lang == "ru" else "Done"])
        for x in items:
            w.writerow([x["what"], x["who"], x.get("to_whom") or "", x.get("due") or "", x.get("meeting_title", ""),
                        x.get("meeting_date", ""), "да" if x.get("status") == "done" and lang == "ru" else
                        "yes" if x.get("status") == "done" else ""])
    return buf.getvalue()
