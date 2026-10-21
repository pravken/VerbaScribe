"""
Screen or window recording by the app itself (FFmpeg gdigrab), for when the window's own screen
picker (getDisplayMedia in WebView2) is not available or fails.

The picture is written by FFmpeg into an MKV file next to the recording: MKV stays playable when
the process is stopped hard, so a crash loses at most the last second. When recording stops, the
page's microphone and the system audio are joined with it into one MP4 (see app/main.py,
screen_mix_command). Windows only: gdigrab is the GDI screen grabber of FFmpeg's Windows builds.
"""

import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FRAME_RATE = 30

_captures: Dict[str, "ScreenCapture"] = {}
_lock = threading.Lock()


def supported() -> bool:
    return sys.platform == "win32"


def capture_command(ffmpeg: str, target: str, dest: Path) -> List[str]:
    """target: "desktop" (every screen) or the exact title of a window."""
    source = "desktop" if target == "desktop" else f"title={target}"
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "gdigrab", "-framerate", str(FRAME_RATE), "-draw_mouse", "1", "-i", source,
        # Even width and height for H.264; ultrafast keeps the CPU free for the rest of the app
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
        str(dest),
    ]


class ScreenCapture:
    def __init__(self, path: Path, target: str):
        self.id = uuid.uuid4().hex
        self.path = path
        self.target = target
        self.started_at = 0.0
        self.error = ""
        self.proc: Optional[subprocess.Popen] = None

    def start(self, ffmpeg: str) -> None:
        self.proc = subprocess.Popen(
            capture_command(ffmpeg, self.target, self.path),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=_NO_WINDOW,
        )
        self.started_at = time.time()
        # FFmpeg's messages are read all the time: a full pipe (repeated grab warnings over a long
        # recording) would stop it. The last lines are kept for the error message.
        self._tail: List[str] = []
        threading.Thread(target=self._drain, daemon=True, name="screen_stderr").start()
        # A window that cannot be found or a missing grabber ends FFmpeg right away
        time.sleep(0.6)
        if self.proc.poll() is not None:
            time.sleep(0.1)
            err = "\n".join(self._tail).strip()
            raise RuntimeError(err[-300:] or "FFmpeg завершился сразу после запуска")

    def _drain(self) -> None:
        for raw in self.proc.stderr:
            self._tail = (self._tail + [raw.decode("utf-8", "replace").rstrip()])[-20:]

    def stop(self) -> None:
        """Asks FFmpeg to finish the file ("q" on its input), then ends it if it does not."""
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            self.proc.kill()
            self.proc.wait(timeout=5)


def start_capture(ffmpeg: str, path: Path, target: str) -> ScreenCapture:
    if not supported():
        raise RuntimeError("Запись экрана средствами приложения доступна только в Windows")
    cap = ScreenCapture(path, target or "desktop")
    cap.start(ffmpeg)
    with _lock:
        _captures[cap.id] = cap
    return cap


def finish_capture(capture_id: str) -> Optional[ScreenCapture]:
    with _lock:
        cap = _captures.pop(capture_id, None)
    if cap:
        cap.stop()
    return cap


def list_windows() -> List[Dict[str, Any]]:
    """Visible top-level windows with a title, for the window picker (Windows only)."""
    if not supported():
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    out: List[Dict[str, Any]] = []
    GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title and title not in ("Program Manager",) and not any(w["title"] == title for w in out):
            out.append({"title": title, "minimized": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(on_window, 0)
    return out
