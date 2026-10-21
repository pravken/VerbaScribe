"""
Frees the models the app keeps in memory after they have not been used for a while
(Settings → General, `idle_unload_minutes`; 0 turns it off).

Every use (a job, a summary, a question, live captions, a preload) calls `touch()`. A background
thread checks twice a minute: when nothing is running and the last use is older than the limit,
Whisper, the speaker model and the live-caption worker are released and the Ollama models this app
asked for are unloaded (models other programs loaded into Ollama are left alone). The models load
again on their next use.
"""

import threading
import time
import traceback
from typing import Any, Dict, Optional, Set

import httpx

CHECK_EVERY_SEC = 30.0

_lock = threading.Lock()
_last_used = time.time()
_unloaded_after: Optional[float] = None  # _last_used value the last unload happened for
_ollama_used: Set[str] = set()
_started = False


def touch(ollama_model: Optional[str] = None) -> None:
    global _last_used
    with _lock:
        _last_used = time.time()
        if ollama_model:
            _ollama_used.add(ollama_model)


def limit_minutes() -> int:
    from app.database import get_all_settings

    try:
        return max(0, int(float(get_all_settings().get("idle_unload_minutes") or 0)))
    except ValueError:
        return 0


def ollama_keep_alive() -> Optional[str]:
    """keep_alive for this app's Ollama requests: the same idle limit, so Ollama unloads by itself too."""
    minutes = limit_minutes()
    return f"{minutes}m" if minutes else None


def status() -> Dict[str, Any]:
    minutes = limit_minutes()
    with _lock:
        idle = time.time() - _last_used
    return {"minutes": minutes, "idle_sec": round(idle), "unload_in_sec": max(0, round(minutes * 60 - idle)) if minutes else None}


def _busy() -> bool:
    from app import live, transcriber

    if transcriber.running_job_kind():
        return True
    return live.has_sessions()


def unload_now() -> Dict[str, Any]:
    """Releases everything the app holds; returns what was released."""
    from app import live, transcriber
    from app.diarizer import ecapa_loaded_on, unload_diarizer

    released = []
    if transcriber.whisper_cache_key():
        transcriber.release_whisper_model()
        released.append("whisper")
    if ecapa_loaded_on():
        unload_diarizer()
        released.append("speakers")
    worker = live._worker
    if worker is not None and worker.proc is not None:
        with worker.lock:
            worker._kill()
        released.append("live")
    try:
        from app.vram import _ollama_base, ollama_loaded

        with _lock:
            ours = set(_ollama_used)
        for m in ollama_loaded():
            if m["name"] in ours:
                with httpx.Client(timeout=15.0) as client:
                    client.post(f"{_ollama_base()}/api/generate", json={"model": m["name"], "keep_alive": 0})
                released.append(m["name"])
    except Exception:
        traceback.print_exc()
    return {"released": released}


def _loop() -> None:
    global _unloaded_after
    while True:
        time.sleep(CHECK_EVERY_SEC)
        try:
            minutes = limit_minutes()
            if not minutes:
                continue
            with _lock:
                last = _last_used
            if _unloaded_after == last or time.time() - last < minutes * 60:
                continue
            if _busy():
                touch()  # in use right now: count the idle time from the end of it
                continue
            res = unload_now()
            _unloaded_after = last
            if res["released"]:
                print(f"[idle] {minutes} min without use: released {', '.join(res['released'])}", flush=True)
        except Exception:
            traceback.print_exc()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="idle_unload").start()
