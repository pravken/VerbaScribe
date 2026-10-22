"""
What the app stops before its process ends (the window closed, "Quit" in the tray, an update).

The process ends with os._exit (a hanging WebView2 must not keep it alive), and on Windows a child
process outlives its parent: FFmpeg recording the screen would go on writing until the disk is full,
a worker would keep its gigabytes of VRAM. So recordings are finished properly first (the screen
file closed by FFmpeg itself, the system sound file closed), then every child process the app
started for its work (track()) is ended with its own children.
"""

import subprocess
import sys
import threading
import weakref
from typing import Any

_procs: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()
_lock = threading.Lock()
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def track(proc: Any) -> Any:
    """A child process to end when the app ends (returns it, for Popen(...) chains)."""
    with _lock:
        _procs.add(proc)
    return proc


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":  # uv and FFmpeg start processes of their own
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                           creationflags=_NO_WINDOW)
        else:
            proc.kill()
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def stop_all() -> None:
    """Finishes recordings and ends the work processes; never raises."""
    try:
        from app import screen_capture

        with screen_capture._lock:
            caps = list(screen_capture._captures.values())
        for cap in caps:
            try:
                cap.stop()  # "q": FFmpeg writes the end of the file, which stays playable
            except Exception:
                pass
    except Exception:
        pass
    try:
        from app import system_audio

        with system_audio._lock:
            caps = list(system_audio._captures.values())
        for cap in caps:
            try:
                cap.stop()
            except Exception:
                pass
    except Exception:
        pass
    with _lock:
        procs = list(_procs)
    for proc in procs:
        _kill_tree(proc)
