"""
Live captions while recording, optionally translated.

The page sends the recording's audio as 16 kHz mono 16-bit PCM about once a second. The server
keeps the audio that is not yet final and, at most every LIVE_STEP_SEC, transcribes that window
(at most LIVE_WINDOW_SEC) with the Whisper model already in memory. Phrases that end well before
the end of the window are final: they are kept, their audio is dropped and, if asked, they are
translated by Ollama in the background. The rest of the window is shown as a draft line.

After recording stops the usual full transcription of the saved file runs; live captions are only
for following along, so they favour speed (greedy decoding, short window) over accuracy.
"""

import threading
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

import numpy as np

SR = 16000
LIVE_STEP_SEC = 1.2       # transcribe the window at most this often
LIVE_WINDOW_SEC = 20.0    # longest audio kept undecided (forced to final beyond it)
FINAL_MARGIN_SEC = 1.6    # a phrase ending this long before the window end is final
SESSION_IDLE_SEC = 120.0  # sessions without audio for this long are dropped

_sessions: Dict[str, "LiveSession"] = {}
_sessions_lock = threading.Lock()
_model_lock = threading.Lock()


def _live_model():
    """The Whisper model already in memory (whatever a job loaded), else the one from Settings."""
    from app import transcriber
    from app.database import get_all_settings
    from app.hardware import get_hardware_info

    with _model_lock:
        cached = transcriber._whisper_cache.get("model")
        if cached is not None:
            return cached
        settings = get_all_settings()
        hw = get_hardware_info()
        model, _ = transcriber._get_whisper_model(
            settings.get("whisper_model") or "large-v3-turbo",
            settings.get("device") or hw["recommended_device"],
            settings.get("compute_type") or hw["recommended_compute_type"],
        )
        return model


class LiveSession:
    def __init__(self, lang: Optional[str], translate_to: Optional[str]):
        self.id = uuid.uuid4().hex
        self.lang = lang if lang and lang != "auto" else None
        self.translate_to = translate_to or None
        self.audio = np.zeros(0, dtype=np.float32)
        self.offset = 0.0           # recording time (s) of self.audio[0]
        self.received = 0.0         # seconds of audio received in total
        self.last_run_at = 0.0      # self.received when the window was last transcribed
        self.lines: List[Dict[str, Any]] = []
        self.partial = ""
        self.error = ""
        self.touched = time.time()
        self._busy = threading.Lock()
        self._tx_lock = threading.Lock()
        self._next_id = 1

    # -------------------------------------------------------------- audio in
    def add_pcm16(self, data: bytes) -> None:
        if len(data) % 2:
            data = data[:-1]
        chunk = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        self.audio = np.concatenate([self.audio, chunk])
        self.received += chunk.size / SR
        self.touched = time.time()

    # -------------------------------------------------------------- decoding
    def step(self) -> None:
        """Transcribes the undecided window if enough new audio arrived and nothing else is running."""
        if self.received - self.last_run_at < LIVE_STEP_SEC or not self._busy.acquire(blocking=False):
            return
        try:
            self.last_run_at = self.received
            self._transcribe_window()
        except Exception as e:  # a failed step must not end the recording
            traceback.print_exc()
            self.error = str(e)[:200]
        finally:
            self._busy.release()

    def _transcribe_window(self) -> None:
        window = self.audio
        win_sec = window.size / SR
        if win_sec < 0.8:
            return
        model = _live_model()
        segments, info = model.transcribe(
            window,
            language=self.lang,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            without_timestamps=False,
        )
        segs = [s for s in segments if (s.text or "").strip()]
        if self.lang is None and getattr(info, "language", None) and getattr(info, "language_probability", 0) > 0.7:
            self.lang = info.language  # keep the language once it is clear: detection per window flickers
        force = win_sec > LIVE_WINDOW_SEC
        final_until = 0.0
        new_lines = []
        for s in segs:
            if s.end <= win_sec - FINAL_MARGIN_SEC or (force and s.end <= win_sec - 0.3):
                new_lines.append({
                    "id": self._next_id, "text": s.text.strip(),
                    "start": round(self.offset + s.start, 2), "end": round(self.offset + s.end, 2),
                    "translation": None,
                })
                self._next_id += 1
                final_until = s.end
            else:
                break
        if final_until > 0:
            cut = int(final_until * SR)
            self.audio = self.audio[cut:]
            self.offset += cut / SR
        elif force:
            # Nothing ended in a long window (one endless phrase): keep only its last part
            cut = int((win_sec - LIVE_WINDOW_SEC / 2) * SR)
            self.audio = self.audio[cut:]
            self.offset += cut / SR
        self.lines.extend(new_lines)
        # Everything after the final phrases is the draft line
        self.partial = " ".join(s.text.strip() for s in segs[len(new_lines):]).strip()
        if new_lines and self.translate_to:
            threading.Thread(target=self._translate, args=(new_lines,), daemon=True).start()

    def _translate(self, lines: List[Dict[str, Any]]) -> None:
        from app.dubbing import DUB_LANGUAGES
        from app.llm_translate import resolve_ollama, translate_texts

        with self._tx_lock:  # in order, one request at a time
            try:
                texts = [ln["text"] for ln in lines]
                src = DUB_LANGUAGES.get(self.lang or "", "the source language")
                dst = DUB_LANGUAGES.get(self.translate_to, self.translate_to)
                base_url, model = resolve_ollama()
                out = translate_texts(texts, src, dst, base_url=base_url, model=model, style="text",
                                      check_cancel=lambda: None, tgt_code=self.translate_to)
                for ln, tr in zip(lines, out):
                    ln["translation"] = tr
            except Exception as e:
                traceback.print_exc()
                self.error = f"Перевод: {str(e)[:160]}"

    # -------------------------------------------------------------- out
    def state(self, since_id: int = 0) -> Dict[str, Any]:
        return {
            "session": self.id,
            "lang": self.lang,
            "lines": [ln for ln in self.lines if ln["id"] > since_id or ln["translation"] is None],
            "translations": {ln["id"]: ln["translation"] for ln in self.lines if ln["translation"]},
            "partial": self.partial,
            "error": self.error,
        }


def start_session(lang: Optional[str], translate_to: Optional[str]) -> LiveSession:
    _drop_idle()
    s = LiveSession(lang, translate_to)
    with _sessions_lock:
        _sessions[s.id] = s
    # Load the model now, so the first captions do not wait for it
    threading.Thread(target=_live_model, daemon=True).start()
    return s


def get_session(session_id: str) -> Optional[LiveSession]:
    with _sessions_lock:
        return _sessions.get(session_id)


def stop_session(session_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(session_id, None)


def _drop_idle() -> None:
    now = time.time()
    with _sessions_lock:
        for sid in [k for k, s in _sessions.items() if now - s.touched > SESSION_IDLE_SEC]:
            _sessions.pop(sid, None)
