"""
Dictation into any program: a system-wide hotkey (Ctrl+Alt+D by default) starts recording the
microphone; the same keys again stop it, the speech is recognized and the text is typed into the
window that has the focus (through the clipboard and Ctrl+V; the clipboard is put back after).

Recognition uses the models already downloaded: GigaAM v3 for Russian, Parakeet for other
languages (both on the CPU, a second or two), otherwise the live-caption Whisper worker. Nothing is
saved in the library.
"""

import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional

import numpy as np

SR = 16000
MAX_SEC = 300  # a forgotten dictation stops by itself

_state: Dict[str, Any] = {"active": False, "error": "", "last_text": "", "started_at": 0.0}
_lock = threading.Lock()
_chunks: List[np.ndarray] = []
_thread: Optional[threading.Thread] = None


def status() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def toggle(notify=None) -> str:
    """Starts or stops (and types) a dictation; returns "started" or "stopped"."""
    with _lock:
        active = _state["active"]
    if active:
        threading.Thread(target=_finish, args=(notify,), daemon=True, name="dictation_finish").start()
        return "stopped"
    _start()
    return "started"


def _start() -> None:
    global _thread
    with _lock:
        _chunks.clear()
        _state.update(active=True, error="", started_at=time.time())
    _thread = threading.Thread(target=_record, daemon=True, name="dictation_mic")
    _thread.start()


def _record() -> None:
    try:
        import soundcard as sc

        mic = sc.default_microphone()
        with mic.recorder(samplerate=SR, channels=1, blocksize=SR // 10) as rec:
            while True:
                with _lock:
                    if not _state["active"] or time.time() - _state["started_at"] > MAX_SEC:
                        _state["active"] = False
                        break
                block = rec.record(numframes=SR // 10)
                with _lock:
                    _chunks.append(np.asarray(block, dtype=np.float32).reshape(-1))
    except Exception as e:
        traceback.print_exc()
        with _lock:
            _state.update(active=False, error=f"Микрофон: {str(e)[:200]}")


def _finish(notify=None) -> None:
    with _lock:
        _state["active"] = False
    if _thread is not None:
        _thread.join(timeout=2)
    with _lock:
        audio = np.concatenate(_chunks) if _chunks else np.zeros(0, dtype=np.float32)
        _chunks.clear()
    try:
        text = recognize(audio) if audio.size > SR // 2 else ""
        with _lock:
            _state.update(last_text=text, error="" if text else "Речь не распознана")
        if text:
            type_text(text)
        if notify:
            notify("Диктовка", text[:120] if text else "Речь не распознана")
    except Exception as e:
        traceback.print_exc()
        with _lock:
            _state["error"] = str(e)[:200]
        if notify:
            notify("Диктовка", f"Не получилось: {str(e)[:120]}")


def _language() -> Optional[str]:
    from app.database import get_all_settings

    lang = (get_all_settings().get("dictation_language") or get_all_settings().get("language") or "auto").strip()
    return None if lang in ("", "auto") else lang


def recognize(audio: np.ndarray) -> str:
    from app import asr_onnx, live
    from app.database import get_all_settings

    lang = _language()
    model = live.caption_onnx_model({**get_all_settings(), "live_whisper_model": "auto"}, lang)
    if model:
        parts = asr_onnx.recognize_array(model, audio)
        return " ".join(t for _, _, t in parts).strip()
    segs, _lang, _prob = live._caption_worker().transcribe(audio, lang)
    return " ".join(s.text.strip() for s in segs).strip()


# ---- typing into the focused window (Windows) ----
def type_text(text: str) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    # Handles are 64-bit: without argtypes ctypes passes them as a 32-bit int and fails (OverflowError)
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002

    def read_clip() -> Optional[str]:
        if not user32.OpenClipboard(None):
            return None
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = kernel32.GlobalLock(h)
            try:
                return ctypes.wstring_at(p) if p else None
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()

    def write_clip(value: str) -> bool:
        data = ctypes.create_unicode_buffer(value)
        size = ctypes.sizeof(data)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        for _ in range(10):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return False
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, h)
        finally:
            user32.CloseClipboard()
        return True

    previous = read_clip()
    if not write_clip(text + " "):
        return
    VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x0002
    for vk in (0x12, 0x10):  # Alt and Shift of the hotkey may still be held
        user32.keybd_event(vk, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)
    time.sleep(0.4)  # the program reads the clipboard before it is put back
    if previous is not None:
        write_clip(previous)
