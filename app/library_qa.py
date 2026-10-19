"""
Questions about the whole library ("what was decided about the release last week?").

The question's keywords are looked up in the full-text index of all transcripts (segments_fts,
ranked by bm25); the best fragments, each with its neighbouring lines and the recording's date,
go to the local Ollama model, which answers only from them and cites fragments as [n]. The
answer comes back with the list of fragments, so every citation opens the recording at that moment.
"""

import re
import sqlite3
from typing import Any, Dict, List, Optional

from app.audio_utils import format_timestamp
from app.database import _db_lock, get_all_settings, get_connection, get_meeting

MAX_FRAGMENTS = 14
MAX_CONTEXT_CHARS = 11000
_STOPWORDS = set(
    """
    что как где когда кто какой какая какие каких чем это этот эта эти тот там тут был была были было
    будет есть ли же бы не ни на по за из для при про под над без или и а но то так уже еще ещё мы вы
    они он она оно его ее её их нам вам мне меня тебя себя все всё всех весь вся очень можно нужно надо
    свой своя свои который которая которые прошлой прошлый прошлую неделе неделю году месяце сказал
    сказали говорил говорили решили решение решения
    what which who whom whose when where why how the a an of to in on at for with about from by is are
    was were be been it this that these those and or but not do does did said say we you they he she
    """.split()
)


def _terms(question: str) -> List[str]:
    """Keywords of the question as FTS prefix terms; long words are cut to a stem-like prefix so
    'релиза' also finds 'релиз', 'релизе'."""
    words = [w for w in re.findall(r"\w+", question.casefold()) if len(w) >= 3 and w not in _STOPWORDS]
    out: List[str] = []
    for w in words:
        # A crude stem that drops the usual endings: срокам -> срок, релиза -> рели, prefixes -> prefi
        stem = w if len(w) <= 5 else w[: len(w) - 2] if len(w) <= 7 else w[: len(w) - 3]
        if stem not in out:
            out.append(stem)
    return out[:10]


def retrieve(question: str) -> List[Dict[str, Any]]:
    terms = _terms(question)
    if not terms:
        return []
    match = " OR ".join(f'"{t}"*' for t in terms)
    with _db_lock:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT meeting_id, segment_id, start, bm25(segments_fts) AS score
                FROM segments_fts WHERE segments_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (match, MAX_FRAGMENTS * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        conn.close()

    meetings: Dict[int, Optional[Dict[str, Any]]] = {}
    fragments: List[Dict[str, Any]] = []
    seen = set()
    for r in rows:
        mid, sid = int(r["meeting_id"]), int(r["segment_id"])
        if mid not in meetings:
            meetings[mid] = get_meeting(mid)
        m = meetings[mid]
        if not m:
            continue
        segs = m.get("segments") or []
        idx = next((i for i, s in enumerate(segs) if int(s.get("id", -1)) == sid), None)
        if idx is None:
            continue
        # The hit with one line before and after: answers often need the reply or the question
        lo, hi = max(0, idx - 1), min(len(segs), idx + 2)
        key = (mid, lo)
        if key in seen:
            continue
        seen.add(key)
        speakers = m.get("speakers") or {}
        lines = [
            f"{(speakers.get(s.get('speaker')) or {}).get('name') or s.get('speaker')}: {(s.get('text') or '').strip()}"
            for s in segs[lo:hi]
        ]
        seg = segs[idx]
        fragments.append({
            "meeting_id": mid,
            "title": m.get("title") or "",
            "date": (m.get("created_at") or "")[:10],
            "start": float(seg.get("start") or 0.0),
            "speaker": (speakers.get(seg.get("speaker")) or {}).get("name") or seg.get("speaker"),
            "text": (seg.get("text") or "").strip(),
            "context": "\n".join(lines),
        })
        if len(fragments) >= MAX_FRAGMENTS:
            break
    return fragments


def ask_library(question: str, answer_lang: str = "ru", model_name: Optional[str] = None) -> Dict[str, Any]:
    from app.ollama_client import _lang_phrase, _ollama_chat, ensure_ollama_running, get_ollama_status

    question = (question or "").strip()[:1000]
    if not question:
        raise ValueError("Пустой вопрос")
    fragments = retrieve(question)
    if not fragments:
        return {"answer": "", "sources": [], "found": False}

    settings = get_all_settings()
    base_url = (settings.get("ollama_url") or "http://127.0.0.1:11434").replace("localhost", "127.0.0.1").rstrip("/")
    model = model_name or settings.get("ollama_model") or "qwen2.5:7b"
    ensure_ollama_running(base_url)  # an explicit user action
    status = get_ollama_status(base_url)
    if not status["running"] or not status["models"]:
        raise RuntimeError("Ollama не запущена или в ней нет моделей. Откройте «Настройки» и скачайте модель.")
    if model not in status["models"]:
        same = [m for m in status["models"] if m.startswith(model.split(":")[0])]
        model = same[0] if same else status["models"][0]

    blocks, used = [], 0
    for n, f in enumerate(fragments, start=1):
        block = f"[{n}] «{f['title']}», {f['date']}, {format_timestamp(f['start'])}\n{f['context']}"
        if used + len(block) > MAX_CONTEXT_CHARS:
            fragments = fragments[: n - 1]
            break
        blocks.append(block)
        used += len(block)

    lang = _lang_phrase(answer_lang)
    system = (
        "Ты отвечаешь на вопросы по медиатеке расшифровок записей в программе VerbaScribe. "
        "Используй ТОЛЬКО приведённые фрагменты. Ничего не додумывай. "
        "После каждого утверждения ставь ссылку на фрагмент в квадратных скобках, например [2] или [1][3]. "
        "Если во фрагментах нет ответа, прямо так и скажи. "
        f"Отвечай {lang}, кратко и по делу, в формате Markdown без эмодзи."
    )
    user = "Фрагменты расшифровок:\n\n" + "\n\n".join(blocks) + f"\n\nВопрос: {question}"
    answer = _ollama_chat(base_url, model, system, user)
    sources = [
        {k: f[k] for k in ("meeting_id", "title", "date", "start", "speaker", "text")} | {"n": n}
        for n, f in enumerate(fragments, start=1)
    ]
    return {"answer": answer, "sources": sources, "found": True, "model": model}
