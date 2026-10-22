"""
What the app is doing right now, in one place: for the tray icon and its tooltip.

The job queue and live caption sessions live in this process. Recording happens in the page
(MediaRecorder), so the page reports its start and end through /api/ui/state.
"""

import time
from typing import Any, Dict, List

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


def background() -> List[str]:
    """Work outside the queue that closing the app would cut off: downloads, installs, a backup."""
    out: List[str] = []

    def check(label: str, active) -> None:
        try:
            if active():
                out.append(label)
        except Exception:
            pass

    from app import asr_onnx, backup, dictation, dub_setup, ffmpeg_manager, model_manager, onnx_gpu

    check("Скачивание модели", lambda: model_manager._setup_state.get("active")
          or any(d.get("active") for d in asr_onnx._downloads.values()))
    check("Скачивание FFmpeg", lambda: ffmpeg_manager._state.get("active"))
    check("Установка ускорения видеокарты", lambda: onnx_gpu._state.get("installing"))
    check("Установка дубляжа", lambda: dub_setup._state.get("active"))
    check("Резервная копия", lambda: backup.busy > 0)
    check("Диктовка", lambda: dictation._state.get("active"))
    check("Скачивание по ссылке", _downloading)
    return out


def _downloading() -> bool:
    from app.database import _db_lock, get_connection

    with _db_lock:
        conn = get_connection()
        row = conn.execute("SELECT 1 FROM meetings WHERE status = 'downloading' LIMIT 1").fetchone()
        conn.close()
    return row is not None


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
        others = background()
        text = " · ".join(others) if others else "Готово к работе"
        state = "busy" if others else "idle"
    if waiting:
        text += f" · в очереди: {waiting}"
    return {"state": state, "text": text, "queue": queue, "recording": _ui["recording"]}
