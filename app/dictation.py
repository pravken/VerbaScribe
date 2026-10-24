"""
Dictation into any program: a system-wide hotkey (Ctrl+Alt+D by default) starts recording the
microphone; the same keys again stop it, the speech is recognized and the text is typed into the
window that has the focus (through the clipboard and Ctrl+V; the clipboard is put back after).

Recognition uses the models already downloaded: GigaAM v3 for Russian, Parakeet for other
languages (both on the CPU, a second or two), otherwise the live-caption Whisper worker. Each
dictation is also kept in the library (folder "Dictations", local analysis only): the sound, the
text and its translation; off in Settings ("dictation_save").

Dictation with translation has a hotkey of its own (Ctrl+Alt+T by default): the recognized text is
translated into the chosen language before it is typed. The local Ollama model translates; a cloud
model only when the user allowed it for dictation (what is dictated can be anything, a password
included). When the translation fails, the original is typed and the notice says so: nothing that
was said is lost.
"""

import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional

import numpy as np

SR = 16000
MAX_SEC = 300  # a forgotten dictation stops by itself

_state: Dict[str, Any] = {"active": False, "busy": False, "error": "", "last_text": "", "started_at": 0.0, "translate_to": ""}
TRANSLATE_TIMEOUT_SEC = 30.0  # the first translation may wait for Ollama to load its model
_lock = threading.Lock()
_chunks: List[np.ndarray] = []
_thread: Optional[threading.Thread] = None


def status() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def toggle(notify=None, translate: bool = False, type_out: bool = True) -> str:
    """Starts or stops (and types) a dictation; returns "started" or "stopped". `translate`: the
    dictation with translation (its own hotkey); either hotkey stops a running dictation.
    `type_out=False` (the button of the home page): the text is only kept, not typed, since the
    window with the focus is VerbaScribe itself; the page shows it (status()["last_text"])."""
    with _lock:
        active = _state["active"]
    if active:
        threading.Thread(target=_finish, args=(notify, type_out), daemon=True, name="dictation_finish").start()
        return "stopped"
    _start(translate_target() if translate else "")
    return "started"


def cancel() -> None:
    """Stops a dictation and throws its sound away: nothing is recognized, typed or kept."""
    with _lock:
        _state.update(active=False, error="")
    if _thread is not None:
        _thread.join(timeout=2)
    with _lock:
        _chunks.clear()


def translate_target() -> str:
    from app.database import get_all_settings

    return (get_all_settings().get("dictation_translate_to") or "en").strip()


def _start(translate_to: str = "") -> None:
    global _thread
    with _lock:
        _chunks.clear()
        _state.update(active=True, error="", last_text="", busy=False, started_at=time.time(), translate_to=translate_to)
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


def _finish(notify=None, type_out: bool = True) -> None:
    with _lock:
        _state.update(active=False, busy=True)
    if _thread is not None:
        _thread.join(timeout=2)
    with _lock:
        audio = np.concatenate(_chunks) if _chunks else np.zeros(0, dtype=np.float32)
        _chunks.clear()
        translate_to = _state.get("translate_to") or ""
    try:
        text, model = recognize(audio) if audio.size > SR // 2 else ("", "")
        original, note = text, ""
        if text and translate_to:
            text, note = translate(text, translate_to)
        with _lock:
            _state.update(last_text=text, busy=False, error="" if text else "Речь не распознана")
        if text:
            if type_out:
                type_text(text)
            # After the typing, so the text is not held up; the translation is kept only when it is one
            threading.Thread(target=save_to_library, args=(audio, original, model, translate_to if text != original else "", text),
                             daemon=True, name="dictation_save").start()
        if notify:
            if not text:
                notify("Диктовка", "Речь не распознана")
            elif note:
                notify("Диктовка: вставлено без перевода", f"{note}. {text[:100]}", warning=True)
            else:
                notify("Диктовка с переводом" if translate_to else "Диктовка", text[:120])
    except Exception as e:
        traceback.print_exc()
        with _lock:
            _state.update(error=str(e)[:200], busy=False)
        if notify:
            notify("Диктовка", f"Не получилось: {str(e)[:120]}")


_DICTATION_PROMPT = (
    "You translate dictated text into {tgt}. The user's message is what a person dictated: an e-mail, a "
    "message, a note or a sentence of a document. Translate it faithfully and naturally as written {tgt}, "
    "keeping names, numbers, links and the paragraph breaks. If it is already in {tgt}, return it unchanged. "
    "Output only the translation: no quotes, notes or explanations."
)


def translate(text: str, target: str) -> "tuple[str, str]":
    """(text to type, note). The note is empty when the text was translated or already in `target`;
    otherwise it says why the original is typed."""
    from app import llm
    from app.database import get_all_settings
    from app.live_translate import phrase_language
    from app.translation import TEXT_LANGUAGES

    if phrase_language(text) == target[:2]:
        return text, ""
    name = TEXT_LANGUAGES.get(target, target)
    settings = get_all_settings()
    allow_cloud = (settings.get("dictation_translate_cloud") or "false").lower() == "true"
    try:
        # Without the dictation's own permission the cloud model of the settings gives way to Ollama
        tgt = llm.target(None, local_only=not allow_cloud)
    except Exception as e:
        return text, f"Перевод недоступен: {str(e)[:100]}"
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    pool = ThreadPoolExecutor(1)
    try:
        answer = pool.submit(tgt.chat, _DICTATION_PROMPT.format(tgt=name), text).result(TRANSLATE_TIMEOUT_SEC)
    except FutureTimeout:
        return text, f"модель не ответила за {int(TRANSLATE_TIMEOUT_SEC)} с"
    except Exception as e:
        return text, f"Перевод не удался: {str(e)[:100]}"
    finally:
        pool.shutdown(wait=False)  # a late answer is dropped
    from app.llm_text import strip_think

    out = strip_think(answer or "").strip().strip('"«»`').strip()
    if not out:
        return text, "модель вернула пустой ответ"
    return out, ""


def _language() -> Optional[str]:
    from app.database import get_all_settings

    lang = (get_all_settings().get("dictation_language") or get_all_settings().get("language") or "auto").strip()
    return None if lang in ("", "auto") else lang


def recognize(audio: np.ndarray) -> "tuple[str, str]":
    """(text, the model that made it)."""
    from app import asr_onnx, live
    from app.database import get_all_settings
    from app.hardware import get_hardware_info

    lang = _language()
    settings = get_all_settings()
    model = live.caption_onnx_model({**settings, "live_whisper_model": "auto"}, lang)
    if model:
        parts = asr_onnx.recognize_array(model, audio)
        return " ".join(t for _, _, t in parts).strip(), model
    segs, _lang, _prob = live._caption_worker().transcribe(audio, lang)
    return " ".join(s.text.strip() for s in segs).strip(), live.caption_model(settings, get_hardware_info())


def save_to_library(audio: np.ndarray, text: str, model: str, translated_to: str = "", translation: str = "") -> Optional[int]:
    """Keeps a dictation as a recording of the library, in its folder "Dictations": the sound
    (16 kHz WAV), the recognized text and, for a dictation with translation, the typed translation as
    the recording's translation. Returns its id (None when it is off or failed: the typing is done)."""
    import uuid
    import wave
    from datetime import datetime

    from app.database import (create_meeting, get_all_settings, save_meeting_results, save_meeting_translation,
                              update_meeting_progress)
    from app.naming import in_date_folder
    from app.paths import recordings_dir

    settings = get_all_settings()
    if (settings.get("dictation_save") or "true").lower() != "true" or not text or not audio.size:
        return None
    try:
        now = datetime.now()
        folder = in_date_folder(recordings_dir(settings.get("recordings_dir") or ""), now)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid.uuid4().hex[:8]}_dictation_{now:%Y%m%d_%H%M%S}.wav"
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm)
        words = text.split()
        title = "Диктовка: " + " ".join(words[:6]) + ("…" if len(words) > 6 else "")
        lang = _language() or ""
        m = create_meeting(title=title, filename=path.name, media_path=str(path), language=lang or "auto", model_name=model,
                           source="dictation", owns_media=True, recorded_at=now.strftime("%Y-%m-%d %H:%M:%S"), has_video=False)
        duration = round(audio.size / SR, 2)
        segments = [{"id": 0, "start": 0.0, "end": duration, "speaker": "SPEAKER_00", "text": text}]
        save_meeting_results(m["id"], speakers={"SPEAKER_00": {"name": "Я", "color": "#0A84FF"}}, segments=segments,
                             language=lang or None, transcript_model=model)
        if translated_to and translation:
            from app.translation import merge_translated_items

            save_meeting_translation(m["id"], translated_to, merge_translated_items(
                None, segments, {0: translation}, model="dictation", source_lang=lang or "auto"))
        update_meeting_progress(m["id"], status="completed", progress=100, stage_text="", duration=duration)
        return m["id"]
    except Exception:
        traceback.print_exc()
        return None


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
