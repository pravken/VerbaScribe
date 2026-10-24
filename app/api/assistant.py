"""The assistant: its conversations (app/assistant_chats.py) and an answer saved as a document.
The answers themselves come from /api/library/ask (app/api/recording.py)."""

import re
from typing import Any, Dict, List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter()


class ChatsPayload(BaseModel):
    chats: List[Dict[str, Any]]


@router.get("/api/assistant/chats")
def api_assistant_chats() -> Dict[str, Any]:
    from app.assistant_chats import load_chats

    return {"chats": load_chats()}


@router.put("/api/assistant/chats")
def api_assistant_save_chats(payload: ChatsPayload) -> Dict[str, Any]:
    from app.assistant_chats import save_chats

    try:
        return {"chats": save_chats(payload.chats)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class AnswerExportPayload(BaseModel):
    fmt: str  # docx | md | txt
    question: str
    answer: str
    sources: List[Dict[str, Any]] = []
    lang: str = "ru"


@router.post("/api/assistant/export")
def api_assistant_export(payload: AnswerExportPayload) -> Response:
    from app.assistant_chats import answer_docx, answer_text

    heading = "Sources" if payload.lang == "en" else "Источники"
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", payload.question).strip()[:80] or "answer"
    if payload.fmt == "docx":
        body = answer_docx(payload.question, payload.answer, payload.sources, heading)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif payload.fmt in ("md", "txt"):
        text = answer_text(payload.question, payload.answer, payload.sources, heading)
        if payload.fmt == "txt":
            text = re.sub(r"^#+ ", "", text, flags=re.M).replace("**", "")
        body = text.encode("utf-8")
        media = "text/markdown; charset=utf-8" if payload.fmt == "md" else "text/plain; charset=utf-8"
    else:
        raise HTTPException(status_code=400, detail="Неизвестный формат файла")
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name + '.' + payload.fmt)}"})
