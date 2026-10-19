"""
Recording of the computer's own sound (what plays in the speakers) without the browser.

In the page, system audio could only come from getDisplayMedia, which asks to pick a window or
screen and to tick "share audio" every time. Here it is captured in the app through WASAPI
loopback of the default output device (the `soundcard` package): no prompt at all. The page still
records the microphone; when recording stops both are mixed into one file (FFmpeg) and queued for
transcription like any import. Live captions get this sound mixed into the page's microphone audio.
"""

import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Dict, Optional

import numpy as np

SR = 48000
BLOCK = SR // 10  # 100 ms

_captures: Dict[str, "SystemCapture"] = {}
_lock = threading.Lock()


class SystemCapture:
    def __init__(self, path: Path):
        self.id = uuid.uuid4().hex
        self.path = path
        self.level = 0.0          # RMS of the last block, for the record meter
        self.error = ""
        self.started_at = 0.0
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._live = np.zeros(0, dtype=np.float32)  # 16 kHz mono, not yet taken by live captions
        self._live_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="system_audio")

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("захват системного звука не начался")
        if self.error:
            raise RuntimeError(self.error)

    def _run(self) -> None:
        import soundcard as sc
        import soundfile as sf

        try:
            speaker = sc.default_speaker()
            loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with sf.SoundFile(str(self.path), "w", samplerate=SR, channels=2, subtype="PCM_16") as out, \
                    loopback.recorder(samplerate=SR, channels=2, blocksize=BLOCK) as rec:
                self.started_at = time.time()
                self._ready.set()
                while not self._stop.is_set():
                    block = rec.record(numframes=BLOCK)
                    if block.ndim == 1:
                        block = np.stack([block, block], axis=1)
                    out.write(block)
                    mono = block.mean(axis=1).astype(np.float32)
                    self.level = float(np.sqrt(np.mean(mono ** 2))) if mono.size else 0.0
                    with self._live_lock:
                        # 48 kHz -> 16 kHz by averaging groups of three samples (enough for captions)
                        n = mono.size // 3 * 3
                        self._live = np.concatenate([self._live, mono[:n].reshape(-1, 3).mean(axis=1)])[-16000 * 30:]
        except Exception as e:
            traceback.print_exc()
            self.error = f"Не удалось записать системный звук: {e}"[:300]
            self._ready.set()

    def take_live(self, count: int) -> np.ndarray:
        """Up to `count` samples (16 kHz mono) captured since the last call, for live captions."""
        with self._live_lock:
            out, self._live = self._live[:count], self._live[count:]
        return out

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def start_capture(path: Path) -> SystemCapture:
    cap = SystemCapture(path)
    cap.start()
    with _lock:
        _captures[cap.id] = cap
    return cap


def get_capture(capture_id: str) -> Optional[SystemCapture]:
    with _lock:
        return _captures.get(capture_id)


def finish_capture(capture_id: str) -> Optional[SystemCapture]:
    with _lock:
        cap = _captures.pop(capture_id, None)
    if cap:
        cap.stop()
    return cap
