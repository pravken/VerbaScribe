"""
In-process event bus for pushing processing progress to the UI over Server-Sent Events.

Worker threads call `publish()`; each open /api/events connection owns a `Subscriber` that lives
on the asyncio loop. Events are coalesced per key (one pending progress update per recording),
so a slow client never builds up a backlog and the worker never blocks.
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_subscribers: List["Subscriber"] = []


class Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._wakeup = asyncio.Event()

    def _push(self, key: str, event: Dict[str, Any]) -> None:
        with self._pending_lock:
            if key in self._pending and event.get("type") == "progress":
                # Keep the newest progress, but never lose a status change in between
                merged = {**self._pending[key], **event}
                self._pending[key] = merged
            else:
                self._pending[key] = event
        try:
            self._loop.call_soon_threadsafe(self._wakeup.set)
        except RuntimeError:
            pass  # loop already closed

    async def next_batch(self, timeout: float) -> List[Dict[str, Any]]:
        """Waits up to `timeout` seconds for events; returns [] on timeout (time for a heartbeat)."""
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout)
        except asyncio.TimeoutError:
            return []
        self._wakeup.clear()
        with self._pending_lock:
            batch = list(self._pending.values())
            self._pending.clear()
        return batch


def subscribe(loop: Optional[asyncio.AbstractEventLoop] = None) -> Subscriber:
    sub = Subscriber(loop or asyncio.get_running_loop())
    with _lock:
        _subscribers.append(sub)
    return sub


def unsubscribe(sub: Subscriber) -> None:
    with _lock:
        if sub in _subscribers:
            _subscribers.remove(sub)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def publish(event: Dict[str, Any], key: Optional[str] = None) -> None:
    """Thread-safe. `key` groups events that may replace each other (default: type + meeting id)."""
    key = key or f"{event.get('type')}:{event.get('id', '')}"
    with _lock:
        subs = list(_subscribers)
    for sub in subs:
        sub._push(key, event)
