"""
Series of recurring meetings (the weekly sync with the same people, a project's calls), and what
they make possible:

- a recording joins a series by itself when it has the same named people as an earlier one (or
  the same title apart from dates and numbers); it can also be moved by hand
- "Since last time": the tasks and decisions of the previous meeting of the series checked
  against this one (done, in progress, not mentioned) plus the new ones
- a brief before the next meeting: open tasks, decisions and questions of the latest meetings of
  a series or with a person
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.database import _db_lock, get_connection, get_meeting, list_meetings, local_only_ids, update_meeting_fields

MIN_SHARED_PEOPLE = 2
MIN_PEOPLE_OVERLAP = 0.5
BRIEF_MEETINGS = 5
_DEFAULT_NAME = re.compile(r"^(Участник|Speaker|SPEAKER)[ _]?\d+$", re.I)


def _init() -> None:
    with _db_lock:
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS series (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.commit()
        conn.close()


def list_series() -> List[Dict[str, Any]]:
    _init()
    with _db_lock:
        conn = get_connection()
        rows = conn.execute(
            "SELECT s.id, s.name, COUNT(m.id) AS count FROM series s LEFT JOIN meetings m ON m.series_id = s.id GROUP BY s.id ORDER BY s.id DESC"
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows if r["count"]]


def create_series(name: str) -> int:
    _init()
    with _db_lock:
        conn = get_connection()
        cur = conn.execute("INSERT INTO series (name, created_at) VALUES (?, ?)",
                           ((name or "Серия").strip()[:120] or "Серия", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        sid = int(cur.lastrowid)
        conn.commit()
        conn.close()
    return sid


def rename_series(series_id: int, name: str) -> None:
    _init()
    with _db_lock:
        conn = get_connection()
        conn.execute("UPDATE series SET name = ? WHERE id = ?", (name.strip()[:120], series_id))
        conn.commit()
        conn.close()


def people(m: Dict[str, Any]) -> Set[str]:
    return {str(v.get("name") or "").strip().lower() for v in (m.get("speakers") or {}).values()
            if str(v.get("name") or "").strip() and not _DEFAULT_NAME.match(str(v.get("name")).strip())}


def title_key(title: str) -> str:
    """A title without dates, numbers and punctuation: "Планёрка 12.05" and "Планёрка 19.05" match."""
    return " ".join(w for w in re.findall(r"[^\W\d_]+", (title or "").casefold()) if len(w) > 2)


def _when(m: Dict[str, Any]) -> str:
    return str(m.get("recorded_at") or m.get("created_at") or "")


def matching_meeting(m: Dict[str, Any], others: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The latest earlier recording that looks like the same recurring meeting."""
    mine, key = people(m), title_key(m.get("title") or "")
    best = None
    for o in others:
        if o["id"] == m["id"] or _when(o) > _when(m):
            continue
        theirs = people(o)
        shared = mine & theirs
        by_people = len(shared) >= MIN_SHARED_PEOPLE and len(shared) / max(1, len(mine | theirs)) >= MIN_PEOPLE_OVERLAP
        by_title = bool(key) and len(key) >= 6 and key == title_key(o.get("title") or "")
        if (by_people or by_title) and (best is None or _when(o) > _when(best)):
            best = o
    return best


def auto_assign(meeting_id: int) -> Optional[int]:
    """Puts a just transcribed recording into the series of a matching earlier one."""
    m = get_meeting(meeting_id)
    if not m or m.get("series_id"):
        return m.get("series_id") if m else None
    rows = [r for r in list_meetings() if r.get("status") == "completed"]
    other = matching_meeting(m, rows)
    if not other:
        return None
    sid = other.get("series_id")
    if not sid:
        shared = sorted(people(m) & people(other))
        name = ", ".join(n.title() for n in shared) if shared else (other.get("title") or "Серия")
        sid = create_series(name)
        update_meeting_fields(other["id"], series_id=sid)
    update_meeting_fields(meeting_id, series_id=sid)
    return int(sid)


def series_meetings(series_id: int) -> List[Dict[str, Any]]:
    return sorted((r for r in list_meetings() if r.get("series_id") == series_id), key=_when)


def previous_meeting(m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not m.get("series_id"):
        return None
    earlier = [r for r in series_meetings(int(m["series_id"])) if _when(r) < _when(m) and r["id"] != m["id"]]
    return get_meeting(earlier[-1]["id"]) if earlier else None


def _outcomes(m: Dict[str, Any]) -> str:
    from app.ollama_client import build_transcript_lines

    summ = m.get("summaries") or {}
    if summ.get("tasks") or summ.get("protocol"):
        return "\n\n".join(x for x in (summ.get("tasks"), summ.get("protocol")) if x)[:6000]
    return "\n".join(build_transcript_lines(m.get("segments") or [], m.get("speakers") or {}))[:6000]


def since_last_time(meeting_id: int, model_name: Optional[str] = None, answer_lang: str = "ru") -> str:
    from app.database import is_local_only
    from app.llm import target
    from app.ollama_client import _lang_phrase, build_transcript_lines

    m = get_meeting(meeting_id)
    if not m:
        raise ValueError("Запись не найдена")
    prev = previous_meeting(m)
    if not prev:
        raise ValueError("В серии нет более ранней встречи")
    llm = target(model_name, local_only=is_local_only(m) or is_local_only(prev))
    now_text = "\n".join(build_transcript_lines(m.get("segments") or [], m.get("speakers") or {}))[: max(8000, llm.max_chars - 9000)]
    system = (
        "Ты сравниваешь две встречи одной серии. По материалам прошлой встречи найди её задачи, договорённости и открытые "
        "вопросы и проверь по расшифровке новой встречи, что с ними стало. Структура в Markdown: «## Выполнено», "
        "«## В работе», «## Не обсуждали» (задачи прошлой встречи, о которых в новой не сказано), «## Новое на этой встрече». "
        "Для каждого пункта — кто ответственный, если сказано, и отметка времени новой встречи в виде [MM:SS], если пункт "
        f"там упомянут. Используй только материалы. Без эмодзи. Пиши {_lang_phrase(answer_lang)}."
    )
    from app import commitments

    earlier = [x for x in commitments.open_for_series(int(m["series_id"])) if x["meeting_id"] != m["id"]]
    if llm.is_cloud:  # the tasks of the series' private meetings (and their titles) stay on this computer
        private = local_only_ids()
        earlier = [x for x in earlier if x["meeting_id"] not in private]
    promised = (f"Открытые задачи из прошлых встреч серии (проверь каждую):\n{commitments.as_text(earlier)}\n\n"
                if earlier else "")
    user = (f"{promised}Прошлая встреча «{prev.get('title')}» ({_when(prev)[:10]}):\n{_outcomes(prev)}\n\n"
            f"Новая встреча «{m.get('title')}» ({_when(m)[:10]}), расшифровка:\n{now_text}")
    return llm.chat(system, user)


def brief(series_id: Optional[int] = None, person: Optional[str] = None, model_name: Optional[str] = None,
          answer_lang: str = "ru") -> Dict[str, Any]:
    """Before the next meeting: what is open from the latest meetings of a series or with a person."""
    from app.llm import target
    from app.ollama_client import _lang_phrase

    if series_id is not None:
        rows = series_meetings(series_id)
    elif person:
        p = person.strip().lower()
        rows = sorted((r for r in list_meetings() if r.get("status") == "completed" and p in people(r)), key=_when)
    else:
        raise ValueError("Выберите серию или человека")
    rows = rows[-BRIEF_MEETINGS:]
    if not rows:
        raise ValueError("Нет встреч для справки")
    llm = target(model_name)
    excluded = 0
    if llm.is_cloud:
        private = local_only_ids()
        kept = [r for r in rows if r["id"] not in private]
        excluded = len(rows) - len(kept)
        if not kept:
            llm, excluded, kept = target(None if model_name == "cloud" else model_name, local_only=True), 0, rows
        rows = kept
    meetings = [m for m in (get_meeting(r["id"]) for r in rows) if m]
    per = max(800, (llm.max_chars - 3000) // max(1, len(meetings)))
    blocks = [f"[{n}] «{m.get('title')}», {_when(m)[:10]}\n{_outcomes(m)[:per]}" for n, m in enumerate(meetings, 1)]
    who = f"с участием {person}" if person else "этой серии"
    system = (
        f"Ты готовишь короткую справку перед следующей встречей {who}. По материалам прошлых встреч собери: "
        "«## Открытые задачи» (кто — что — срок), «## Принятые решения», «## Открытые вопросы», «## О чём спросить или напомнить». "
        "Самое свежее важнее старого; выполненное не включай. После каждого пункта ставь ссылку на встречу [n]. "
        f"Только по материалам, без эмодзи. Пиши {_lang_phrase(answer_lang)}."
    )
    from app import commitments

    open_items = commitments.for_meetings([int(mm["id"]) for mm in meetings], status="open")
    promised = (f"Открытые задачи (никто ещё не отметил их выполненными):\n{commitments.as_text(open_items)}\n\n"
                if open_items else "")
    if open_items:
        system += " Отдельным разделом «## Задачи» перечисли открытые задачи: кто, кому, что, срок."
    answer = llm.chat(system, promised + "Материалы встреч (от старых к новым):\n\n" + "\n\n".join(blocks))
    sources = [{"n": n, "meeting_id": int(m["id"]), "title": m.get("title") or "", "date": _when(m)[:10], "start": 0.0,
                "speaker": "", "text": ""} for n, m in enumerate(meetings, 1)]
    return {"answer": answer, "sources": sources, "found": True, "model": llm.label, "cloud": llm.is_cloud,
            "mode": "brief", "count": len(meetings), "excluded_local": excluded}
