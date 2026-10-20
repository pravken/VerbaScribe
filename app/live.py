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

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.paths import APP_DIR

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

SR = 16000
LIVE_STEP_SEC = 1.2       # transcribe the window at most this often
LIVE_WINDOW_SEC = 20.0    # longest audio kept undecided (forced to final beyond it)
FINAL_MARGIN_SEC = 1.6    # a phrase ending this long before the window end is final
SESSION_IDLE_SEC = 120.0  # sessions without audio for this long are dropped

WORKER_IDLE_SEC = 120.0   # the caption worker is closed (VRAM freed) after this long unused

_sessions: Dict[str, "LiveSession"] = {}
_sessions_lock = threading.Lock()


class _Seg:
    __slots__ = ("start", "end", "text")

    def __init__(self, start: float, end: float, text: str):
        self.start, self.end, self.text = start, end, text


class _CaptionWorker:
    """
    The Whisper model for captions runs in app/live_worker.py: if it fails natively (CUDA out of
    memory can abort a process) only captions stop, never the app or the recording.
    """

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.last_used = 0.0
        self.stderr_tail: List[str] = []
        threading.Thread(target=self._reaper, daemon=True, name="live_worker_reaper").start()

    def _start(self) -> None:
        cmd = [sys.executable, "-m", "app.live_worker"]
        self.proc = subprocess.Popen(
            cmd, cwd=str(APP_DIR), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        threading.Thread(target=self._drain, args=(self.proc,), daemon=True).start()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("процесс распознавания не запустился: " + " | ".join(self.stderr_tail[-3:]))
        json.loads(line)

    def _drain(self, proc) -> None:
        for raw in proc.stderr:
            text = raw.decode("utf-8", "replace").rstrip()
            self.stderr_tail = (self.stderr_tail + [text])[-20:]

    def transcribe(self, audio: np.ndarray, lang: Optional[str]) -> Tuple[List[_Seg], Optional[str], float]:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        with self.lock:
            for attempt in range(2):
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        self._start()
                    self.proc.stdin.write((json.dumps({"lang": lang, "bytes": len(pcm)}) + "\n").encode())
                    self.proc.stdin.write(pcm)
                    self.proc.stdin.flush()
                    line = self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("процесс распознавания завершился")
                    res = json.loads(line)
                    self.last_used = time.time()
                    if not res.get("ok"):
                        raise RuntimeError(res.get("error") or "ошибка распознавания")
                    segs = [_Seg(float(a), float(b), str(t)) for a, b, t in res["segments"]]
                    return segs, res.get("language"), float(res.get("prob") or 0.0)
                except (OSError, ValueError, RuntimeError) as e:
                    self._kill()
                    if attempt:
                        raise RuntimeError(f"Субтитры остановлены: {e}") from e
        return [], None, 0.0

    def preload(self) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                try:
                    self._start()
                    self.last_used = time.time()
                except Exception:
                    traceback.print_exc()
                    self._kill()

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc = None

    def _reaper(self) -> None:
        while True:
            time.sleep(15)
            with _sessions_lock:
                active = bool(_sessions)
            if self.proc is not None and not active and time.time() - self.last_used > WORKER_IDLE_SEC:
                with self.lock:
                    self._kill()


_worker: Optional[_CaptionWorker] = None
_worker_lock = threading.Lock()


def _caption_worker() -> _CaptionWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = _CaptionWorker()
        return _worker


class LiveSession:
    def __init__(self, lang: Optional[str], translate_to: Optional[str], capture_id: Optional[str] = None,
                 speed: str = "quality"):
        self.id = uuid.uuid4().hex
        self.capture_id = capture_id
        # The language chosen for speech (None: detected in every window, so a meeting may switch
        # between languages); self.lang is the language heard last, for translation and the UI
        self.speech_lang = lang if lang and lang != "auto" else None
        self.lang = self.speech_lang
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
        self.translator = None
        if translate_to:
            from app.live_translate import LiveTranslator

            # Starts loading the translation model now, while the first phrase is being spoken
            self.translator = LiveTranslator(lambda: self.lang, translate_to, speed)
        self._next_id = 1

    # -------------------------------------------------------------- audio in
    def add_pcm16(self, data: bytes) -> None:
        if len(data) % 2:
            data = data[:-1]
        chunk = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if self.capture_id:
            # System audio captured by the app (app/system_audio.py) joins the page's microphone
            from app.system_audio import get_capture

            cap = get_capture(self.capture_id)
            if cap is not None:
                sys_part = cap.take_live(chunk.size)
                chunk = chunk.copy()
                chunk[: sys_part.size] += sys_part
        self.audio = np.concatenate([self.audio, chunk])
        self.received += chunk.size / SR
        self.touched = time.time()
        from app import idle_unload

        idle_unload.touch()

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
        segs, language, prob = _caption_worker().transcribe(window, self.speech_lang)
        if self.speech_lang:
            self.lang = self.speech_lang
        elif language and prob > 0.5:
            self.lang = language
        self.error = ""
        force = win_sec > LIVE_WINDOW_SEC
        final_until = 0.0
        new_lines = []
        for s in segs:
            if s.end <= win_sec - FINAL_MARGIN_SEC or (force and s.end <= win_sec - 0.3):
                new_lines.append({
                    "id": self._next_id, "text": s.text.strip(), "lang": self.lang,
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
        if self.translator:
            if new_lines:
                self.translator.add_lines(new_lines)
                # The draft translation belonged to the phrase that is final now: the next draft
                # must not be shown with it (the page keeps it on the final line until its own
                # translation arrives)
                self.translator.reset_draft()
            self.translator.set_draft(self.partial)

    def configure(self, translate_to: Optional[str], speed: Optional[str], speech_lang: Optional[str] = None) -> None:
        """Changes the translation (and the speech language) while recording; the last lines are
        translated again."""
        from app.live_translate import LiveTranslator

        if speech_lang is not None:
            self.speech_lang = None if speech_lang in ("", "auto") else speech_lang
            if self.speech_lang:
                self.lang = self.speech_lang
        if not translate_to:
            if self.translator:
                self.translator.stop()
            self.translator, self.translate_to = None, None
            return
        changed = translate_to != self.translate_to
        self.translate_to = translate_to
        if self.translator is None:
            self.translator = LiveTranslator(lambda: self.lang, translate_to, speed or "quality")
            self.translator.add_lines([ln for ln in self.lines[-20:]])
        else:
            self.translator.configure(translate_to, speed, retranslate=self.lines[-20:] if changed else None)
        self.translator.set_draft(self.partial)

    # -------------------------------------------------------------- out
    def state(self, since_id: int = 0) -> Dict[str, Any]:
        return {
            "session": self.id,
            "lang": self.lang,
            "speech_lang": self.speech_lang or "auto",
            "lines": [ln for ln in self.lines if ln["id"] > since_id or ln["translation"] is None],
            "translations": {ln["id"]: ln["translation"] for ln in self.lines if ln["translation"]},
            "partial": self.partial,
            "partial_translation": self.translator.draft_translation if self.translator else "",
            "translation_ready": bool(self.translator and self.translator.ready),
            "error": self.error or (self.translator.error if self.translator else ""),
        }


def start_session(lang: Optional[str], translate_to: Optional[str], capture_id: Optional[str] = None,
                  speed: str = "quality") -> LiveSession:
    _drop_idle()
    s = LiveSession(lang, translate_to, capture_id, speed)
    with _sessions_lock:
        _sessions[s.id] = s
    # Start the caption worker now, so the first captions do not wait for the model to load
    threading.Thread(target=_caption_worker().preload, daemon=True).start()
    return s


def get_session(session_id: str) -> Optional[LiveSession]:
    with _sessions_lock:
        return _sessions.get(session_id)


def stop_session(session_id: str) -> None:
    with _sessions_lock:
        s = _sessions.pop(session_id, None)
    if s and s.translator:
        s.translator.stop()


def _drop_idle() -> None:
    now = time.time()
    with _sessions_lock:
        for sid in [k for k, s in _sessions.items() if now - s.touched > SESSION_IDLE_SEC]:
            _sessions.pop(sid, None)
