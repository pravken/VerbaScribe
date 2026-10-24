"""
The conversations of the assistant (questions about the library): kept in one JSON file of the
data folder, on this computer only. The page sends the whole list after each change; it is checked
and trimmed here, so a bad or huge list cannot fill the disk or break the next start.

An answer can also be saved as a document (Word, Markdown, plain text) with its sources.
"""

import io
import json
import os
import re
import threading
from typing import Any, Dict, List

from app.paths import DATA_DIR

CHATS_FILE = DATA_DIR / "assistant_chats.json"
MAX_CHATS = 200  # the oldest are dropped
MAX_ITEMS = 100  # questions in one conversation
MAX_BYTES = 4 * 1024 * 1024  # the whole file
_lock = threading.Lock()

_ITEM_KEYS = {"id", "question", "scope", "scopeLabel", "status", "data", "error", "at"}
_CHAT_KEYS = {"id", "title", "created", "updated", "model", "items"}


def _clean_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Неверный формат разговора")
    out = {k: v for k, v in item.items() if k in _ITEM_KEYS}
    out["question"] = str(out.get("question") or "")[:1000]
    # an answer still running is saved as stopped: it never comes back after a restart
    if out.get("status") not in ("done", "error", "stopped"):
        out["status"] = "stopped"
    return out


def _clean_chat(chat: Any) -> Dict[str, Any]:
    if not isinstance(chat, dict) or not str(chat.get("id") or "").strip():
        raise ValueError("Неверный формат разговора")
    out = {k: v for k, v in chat.items() if k in _CHAT_KEYS}
    out["id"] = str(out["id"])[:64]
    out["title"] = str(out.get("title") or "")[:200]
    items = out.get("items") or []
    if not isinstance(items, list):
        raise ValueError("Неверный формат разговора")
    out["items"] = [_clean_item(i) for i in items[-MAX_ITEMS:]]
    return out


def clean_chats(chats: Any) -> List[Dict[str, Any]]:
    """The list to store: valid conversations, the newest MAX_CHATS of them."""
    if not isinstance(chats, list):
        raise ValueError("Неверный формат разговора")
    out = [_clean_chat(c) for c in chats]
    out.sort(key=lambda c: str(c.get("updated") or c.get("created") or ""), reverse=True)
    return out[:MAX_CHATS]


def load_chats() -> List[Dict[str, Any]]:
    with _lock:
        try:
            data = json.loads(CHATS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    try:
        return clean_chats(data)
    except ValueError:
        return []


def save_chats(chats: Any) -> List[Dict[str, Any]]:
    clean = clean_chats(chats)
    text = json.dumps(clean, ensure_ascii=False)
    # too big: the oldest conversations go first, then the sources of the old answers
    while len(text.encode("utf-8")) > MAX_BYTES and len(clean) > 1:
        clean = clean[:-1]
        text = json.dumps(clean, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Разговор слишком большой, чтобы его сохранить")
    with _lock:
        CHATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHATS_FILE.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, CHATS_FILE)  # a crash while writing leaves the old file, never half of one
    return clean


def _sources_lines(sources: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for s in sources or []:
        start = float(s.get("start") or 0)
        t = f"{int(start // 3600):02d}:{int(start % 3600 // 60):02d}:{int(start % 60):02d}"
        who = f"{s.get('speaker')}: " if s.get("speaker") else ""
        lines.append(f"[{s.get('n')}] {s.get('title', '')} · {s.get('date', '')} · {t} — {who}{s.get('text', '')}")
    return lines


def answer_text(question: str, answer: str, sources: List[Dict[str, Any]], heading_sources: str) -> str:
    parts = [f"# {question.strip()}", "", answer.strip()]
    lines = _sources_lines(sources)
    if lines:
        parts += ["", f"## {heading_sources}", ""] + [f"- {x}" for x in lines]
    return "\n".join(parts) + "\n"


def answer_docx(question: str, answer: str, sources: List[Dict[str, Any]], heading_sources: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(question.strip() or "—", level=1)
    for raw in (answer or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif re.match(r"^[-*] ", line):
            doc.add_paragraph(re.sub(r"\*\*(.+?)\*\*", r"\1", line[2:]), style="List Bullet")
        else:
            doc.add_paragraph(re.sub(r"\*\*(.+?)\*\*", r"\1", line))
    lines = _sources_lines(sources)
    if lines:
        doc.add_heading(heading_sources, level=2)
        for x in lines:
            doc.add_paragraph(x, style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
