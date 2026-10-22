"""
What the app is doing right now, in one place: for the tray icon and its tooltip.

The job queue and live caption sessions live in this process. Recording happens in the page
(MediaRecorder), so the page reports its start and end through /api/ui/state.
"""

import time
from typing import Any, Dict

_ui: Dict[str, Any] = {"recording": False, "since": 0.0}

KIND_LABELS = {
    "transcribe": "Распознавание",
    "dub": "Дубляж",
    "translate": "Перевод",
    "burn_subs": "Встраивание субтитров",
    "cut": "Нарезка записи",
}


def set_recording(active: bool) -> None:
    _ui.update(recording=bool(active), since=time.time() if active else 0.0)


def snapshot() -> Dict[str, Any]:
    """
    {"state": "idle" | "busy" | "recording", "text": tooltip line, "queue": [...], "recording": bool}.
    Recording wins over processing: it is the thing the user must not forget is running.
    """
    from app.transcriber import queue_snapshot

    try:
        queue = queue_snapshot()
    except Exception:
        queue = []
    running = next((q for q in queue if q.get("state") == "running"), None)
    waiting = sum(1 for q in queue if q.get("state") == "waiting")

    if _ui["recording"]:
        mins, secs = divmod(int(time.time() - _ui["since"]), 60)
        text = f"Идёт запись · {mins:02d}:{secs:02d}"
        state = "recording"
    elif running:
        label = KIND_LABELS.get(running.get("kind"), "Обработка")
        title = (running.get("title") or "")[:40]
        text = f"{label}: {title} · {int(running.get('progress') or 0)}%"
        state = "busy"
    else:
        text = "Готово к работе"
        state = "idle"
    if waiting:
        text += f" · в очереди: {waiting}"
    return {"state": state, "text": text, "queue": queue, "recording": _ui["recording"]}
