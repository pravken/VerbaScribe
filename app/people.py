"""
The people directory ("Все люди"): everyone the library knows of — the named speakers of the
recordings, the people invited to the calendar meetings the recordings were made in, the ones
promises name — and the people and departments added by hand.

Two spellings of one person can be merged: the first name stays, the others become its variants
(aliases), so a later recording, an invitation or a promise with a variant still finds the person.
"Это я" marks the user (one person at most). A person may have a role ("руководитель проекта").
Who the user is lives in the setting my_name (names separated by commas), which the home page and
the tasks read too; the directory's own mark is only a fallback for databases from before it.

Only the hand-made part is stored (table people_directory: added entries, variants, roles, "me");
the rest is read from the library each time, so it never goes stale.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.database import _db_lock, get_all_settings, get_connection, update_settings

KINDS = ("person", "dept")
MAX_NAME = 120
# The calendar's participants of a recording, as app/calendar_ics.apply_to_recording writes them
_CAL_HEAD_RE = re.compile(r"^(?:Участники по календарю|Participants \(calendar\))\s*:\s*(.+)$", re.M)


def my_names() -> List[str]:
    """The user's names from the setting my_name, lower case."""
    return [n.strip().lower() for n in str(get_all_settings().get("my_name") or "").split(",") if n.strip()]


def _set_me(name: str, on: bool) -> None:
    """"Это я" for name: it becomes the user's name, or stops being one of them."""
    names = [n.strip() for n in str(get_all_settings().get("my_name") or "").split(",") if n.strip()]
    if on:
        update_settings({"my_name": name})
    elif any(n.lower() == name.lower() for n in names):
        update_settings({"my_name": ", ".join(n for n in names if n.lower() != name.lower())})


def init_table() -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS people_directory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                kind TEXT NOT NULL DEFAULT 'person',
                role TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '[]',
                is_me INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()


def _clean(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()[:MAX_NAME]


def _rows() -> List[Dict[str, Any]]:
    init_table()
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM people_directory ORDER BY id").fetchall()
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["aliases"] = [a for a in json.loads(d.get("aliases") or "[]") if isinstance(a, str)]
        except ValueError:
            d["aliases"] = []
        d["is_me"] = bool(d["is_me"])
        out.append(d)
    return out


def calendar_attendees(meeting: Dict[str, Any]) -> List[str]:
    """The people invited to the calendar meeting a recording was made in (from its notes)."""
    m = _CAL_HEAD_RE.search(meeting.get("user_notes") or "")
    if not m:
        return []
    names = []
    for part in m.group(1).split(","):
        name = _clean(re.sub(r"<[^>]*>", "", part))  # "Имя <почта>" keeps the name
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return names[:30]


def _alias_map(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """A variant (lower case) → the name it was merged into."""
    out: Dict[str, str] = {}
    for r in rows:
        for a in r["aliases"]:
            out[a.lower()] = r["name"]
    return out


def canonical(name: str, rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """The directory's name for a spelling (itself when it is not a merged variant)."""
    name = _clean(name)
    return _alias_map(rows if rows is not None else _rows()).get(name.lower(), name)


def _library_people() -> Iterable[Dict[str, Any]]:
    from app.voice_profiles import people

    return people()


def _library_meetings() -> List[Dict[str, Any]]:
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT id, user_notes FROM meetings WHERE status = 'completed'").fetchall()
        conn.close()
    return [dict(r) for r in rows]


def _commitment_names() -> List[str]:
    from app.commitments import _init

    _init()
    with _db_lock:
        conn = get_connection()
        rows = conn.execute("SELECT who, to_whom FROM commitments").fetchall()
        conn.close()
    names: List[str] = []
    for r in rows:
        names.extend([r["who"], r["to_whom"]])
    return [n for n in (_clean(x) for x in names) if n]


def directory() -> List[Dict[str, Any]]:
    """Everyone, most present first: [{id, name, kind, role, aliases, is_me, recordings, seconds,
    voice, tasks, calendar, sources}]. id is None for a person nobody edited yet."""
    rows = _rows()
    alias = _alias_map(rows)
    by_key: Dict[str, Dict[str, Any]] = {}

    def entry(name: str) -> Dict[str, Any]:
        name = alias.get(name.lower(), name)
        key = name.lower()
        if key not in by_key:
            by_key[key] = {"id": None, "name": name, "kind": "person", "role": "", "aliases": [], "is_me": False,
                           "recordings": 0, "seconds": 0.0, "voice": False, "tasks": 0, "calendar": 0, "sources": []}
        return by_key[key]

    def source(e: Dict[str, Any], s: str) -> None:
        if s not in e["sources"]:
            e["sources"].append(s)

    for r in rows:
        e = entry(r["name"])
        e.update(id=r["id"], name=r["name"], kind=r["kind"] if r["kind"] in KINDS else "person", role=r["role"] or "",
                 aliases=list(r["aliases"]), is_me=r["is_me"])
        source(e, "manual")
    for p in _library_people():
        e = entry(p["name"])
        e["recordings"] += len(p.get("recordings") or [])
        e["seconds"] = round(e["seconds"] + float(p.get("total_seconds") or 0), 1)
        e["voice"] = e["voice"] or bool(p.get("profile"))
        source(e, "records")
    for m in _library_meetings():
        for name in calendar_attendees(m):
            e = entry(name)
            e["calendar"] += 1
            source(e, "calendar")
    for name in _commitment_names():
        e = entry(name)
        e["tasks"] += 1
        source(e, "tasks")
    me = my_names()
    if me:  # the setting decides; the stored mark only counts while it is empty
        for e in by_key.values():
            e["is_me"] = any(n.lower() in me for n in [e["name"], *e["aliases"]])
    return sorted(by_key.values(), key=lambda e: (not e["is_me"], -(e["recordings"] + e["calendar"] + e["tasks"]), e["name"].lower()))


def _find(conn, name: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM people_directory WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def _ensure(conn, name: str, kind: str = "person") -> int:
    row = _find(conn, name)
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO people_directory (name, kind, created_at) VALUES (?, ?, ?)",
                       (name, kind, datetime.now().isoformat(timespec="seconds")))
    return int(cur.lastrowid)


def add(name: str, kind: str = "person") -> Dict[str, Any]:
    """A person or a department added by hand (an existing one is returned as it is)."""
    name = _clean(name)
    if not name:
        raise ValueError("Укажите имя")
    if kind not in KINDS:
        raise ValueError("Неизвестный вид записи справочника")
    init_table()
    rows = _rows()
    name = canonical(name, rows)
    with _db_lock:
        conn = get_connection()
        try:
            _ensure(conn, name, kind)
            conn.commit()
        finally:
            conn.close()
    return next(e for e in directory() if e["name"].lower() == name.lower())


def update(name: str, role: Optional[str] = None, kind: Optional[str] = None, is_me: Optional[bool] = None,
           new_name: Optional[str] = None) -> Dict[str, Any]:
    """Role, kind, "это я" or a new spelling of a person (the old one stays as a variant)."""
    name = _clean(name)
    if not name:
        raise ValueError("Укажите имя")
    if kind is not None and kind not in KINDS:
        raise ValueError("Неизвестный вид записи справочника")
    init_table()
    name = canonical(name)
    target = name
    with _db_lock:
        conn = get_connection()
        try:
            pid = _ensure(conn, name)
            if role is not None:
                conn.execute("UPDATE people_directory SET role = ? WHERE id = ?", (_clean(role), pid))
            if kind is not None:
                conn.execute("UPDATE people_directory SET kind = ? WHERE id = ?", (kind, pid))
            if is_me is not None:
                if is_me:
                    conn.execute("UPDATE people_directory SET is_me = 0")
                conn.execute("UPDATE people_directory SET is_me = ? WHERE id = ?", (1 if is_me else 0, pid))
            nn = _clean(new_name) if new_name is not None else ""
            if nn and nn.lower() != name.lower():
                if _find(conn, nn):
                    raise ValueError("Такое имя уже есть в справочнике: объедините записи")
                row = _find(conn, name)
                aliases = [a for a in json.loads(row["aliases"] or "[]") if a.lower() != nn.lower()]
                aliases.append(name)
                conn.execute("UPDATE people_directory SET name = ?, aliases = ? WHERE id = ?",
                             (nn, json.dumps(aliases, ensure_ascii=False), pid))
                target = nn
            conn.commit()
        finally:
            conn.close()
    if is_me is not None:
        _set_me(target, is_me)
    elif target.lower() != name.lower() and name.lower() in my_names():  # the user renamed themselves
        _set_me(target, True)
    return next(e for e in directory() if e["name"].lower() == target.lower())


def merge(names: List[str]) -> Dict[str, Any]:
    """The first name stays; the others become its variants (with their own variants and role)."""
    names = [n for n in (_clean(x) for x in names) if n]
    if len(names) < 2:
        raise ValueError("Выберите хотя бы двоих, чтобы объединить")
    init_table()
    rows = _rows()
    names = [canonical(n, rows) for n in names]
    keep = names[0]
    others = [n for n in dict.fromkeys(n.lower() for n in names[1:]) if n != keep.lower()]
    with _db_lock:
        conn = get_connection()
        try:
            pid = _ensure(conn, keep)
            row = _find(conn, keep)
            aliases = list(json.loads(row["aliases"] or "[]"))
            role, me = row["role"], bool(row["is_me"])
            for low in others:
                other = next((n for n in names if n.lower() == low), low)
                orow = _find(conn, other)
                if orow:
                    aliases.extend(json.loads(orow["aliases"] or "[]"))
                    role = role or orow["role"]
                    me = me or bool(orow["is_me"])
                    conn.execute("DELETE FROM people_directory WHERE id = ?", (orow["id"],))
                aliases.append(other)
            seen, clean = set(), []
            for a in aliases:
                if a.lower() not in seen and a.lower() != keep.lower():
                    seen.add(a.lower())
                    clean.append(a)
            conn.execute("UPDATE people_directory SET aliases = ?, role = ?, is_me = ? WHERE id = ?",
                         (json.dumps(clean, ensure_ascii=False), role, 1 if me else 0, pid))
            conn.commit()
        finally:
            conn.close()
    return next(e for e in directory() if e["name"].lower() == keep.lower())


def remove(name: str) -> bool:
    """Forget what was added by hand about a person (the library's own mentions stay)."""
    init_table()
    with _db_lock:
        conn = get_connection()
        cur = conn.execute("DELETE FROM people_directory WHERE name = ?", (_clean(name),))
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def mentions(meeting: Dict[str, Any], people: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """People and departments of the directory named in a recording's text who do not speak in it:
    [{name, kind, count, first_at}], most mentioned first."""
    people = people if people is not None else directory()
    speaking = {_clean((s or {}).get("name")).lower() for s in (meeting.get("speakers") or {}).values()}
    found: Dict[str, Dict[str, Any]] = {}
    for p in people:
        if p["name"].lower() in speaking:
            continue
        spellings = [p["name"]] + list(p.get("aliases") or [])
        # a name as a whole word: "Анна" is not found in "Аннотация"; Russian endings are allowed
        pats = [re.compile(r"(?<![\w-])" + re.escape(s) + r"(?:а|у|ой|ом|е|ы|ю|ем|и)?(?![\w-])", re.I)
                for s in spellings if len(s) >= 3]
        for seg in meeting.get("segments") or []:
            text = seg.get("text") or ""
            n = sum(len(p_.findall(text)) for p_ in pats)
            if not n:
                continue
            e = found.setdefault(p["name"].lower(), {"name": p["name"], "kind": p["kind"], "count": 0,
                                                     "first_at": float(seg.get("start") or 0)})
            e["count"] += n
    return sorted(found.values(), key=lambda e: (-e["count"], e["first_at"]))


# --- The speakers of one recording: merge two into one, split one in two, role and "это я" ---
_SPK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_PALETTE = ["#64D2FF", "#FFB340", "#C9A2FF", "#7EE787", "#FF8A80", "#FFD60A", "#5AC8FA", "#FF9F0A"]


def _check_spk(speaker_id: str) -> None:
    if not _SPK_ID_RE.match(speaker_id or ""):
        raise ValueError("Некорректный идентификатор участника")


def merge_speakers(meeting_id: int, source: str, target: str) -> Optional[Dict[str, Any]]:
    """Every line of `source` becomes a line of `target` (one person counted as two); `source` goes."""
    from app.database import edit_meeting

    _check_spk(source)
    _check_spk(target)
    if source == target:
        raise ValueError("Выберите другого участника")

    def change(m: Dict[str, Any]) -> tuple:
        speakers = m.get("speakers") or {}
        if source not in speakers or target not in speakers:
            raise ValueError("Участник не найден")
        for seg in m.get("segments") or []:
            if seg.get("speaker") == source:
                seg["speaker"] = target
        speakers.pop(source, None)
        m["speakers"] = speakers
        return ("segments", "speakers")

    return edit_meeting(meeting_id, change)


def split_speaker(meeting_id: int, speaker_id: str, positions: List[int], new_name: str) -> Optional[Dict[str, Any]]:
    """The chosen lines (positions in the transcript) of a speaker go to a new person (two people
    counted as one); the others stay."""
    from app.database import edit_meeting

    _check_spk(speaker_id)
    name = _clean(new_name)
    wanted = {int(p) for p in positions}
    if not wanted:
        raise ValueError("Отметьте реплики другого человека")

    def change(m: Dict[str, Any]) -> tuple:
        speakers = m.get("speakers") or {}
        segs = m.get("segments") or []
        if speaker_id not in speakers:
            raise ValueError("Участник не найден")
        own = [i for i, s in enumerate(segs) if s.get("speaker") == speaker_id]
        moved = [i for i in own if i in wanted]
        if not moved:
            raise ValueError("Отметьте реплики другого человека")
        if len(moved) == len(own):
            raise ValueError("У участника должна остаться хотя бы одна реплика")
        n = 1
        while f"SPEAKER_{n:02d}" in speakers:
            n += 1
        new_id = f"SPEAKER_{n:02d}"
        used = {(s or {}).get("color") for s in speakers.values()}
        color = next((c for c in _PALETTE if c not in used), _PALETTE[n % len(_PALETTE)])
        first = segs[moved[0]]
        speakers[new_id] = {"id": new_id, "name": canonical(name) if name else f"Участник {len(speakers) + 1}",
                            "color": color, "sample_start": float(first.get("start") or 0),
                            "sample_end": float(first.get("end") or 0)}
        for i in moved:
            segs[i]["speaker"] = new_id
        m["speakers"] = speakers
        return ("segments", "speakers")

    return edit_meeting(meeting_id, change)


def set_speaker_meta(meeting_id: int, speaker_id: str, role: Optional[str] = None,
                     is_me: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """A speaker's role in this recording and "это я" (one speaker of a recording at most; the
    directory remembers the user's name)."""
    from app.database import edit_meeting

    _check_spk(speaker_id)
    named: List[str] = []

    def change(m: Dict[str, Any]) -> tuple:
        speakers = m.get("speakers") or {}
        spk = speakers.get(speaker_id)
        if spk is None:
            raise ValueError("Участник не найден")
        if role is not None:
            spk["role"] = _clean(role)
        if is_me is not None:
            for s in speakers.values():
                s.pop("is_me", None)
            if is_me:
                spk["is_me"] = True
                named.append(str(spk.get("name") or ""))
        m["speakers"] = speakers
        return ("speakers",)

    m = edit_meeting(meeting_id, change)
    from app.voice_profiles import is_default_name

    if m is not None and named and not is_default_name(named[0]):
        update(named[0], is_me=True)
    return m
