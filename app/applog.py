"""
Log file of the desktop app.

Launched from Verbaliox.exe the app has no visible console, so everything it prints (uvicorn
messages, tracebacks, warnings) would be lost. `start_file_log()` copies stdout and stderr into
`<data>/logs/verbaliox.log` with a time stamp per line, keeps the last few files, and makes
Python write a stack dump there if the process crashes hard.
"""

import faulthandler
import sys
import threading
import time
from pathlib import Path
from typing import Optional, TextIO

from app.paths import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "verbaliox.log"
MAX_BYTES = 2 * 1024 * 1024
KEEP_FILES = 4  # verbaliox.log + .1 ... .3

_lock = threading.Lock()
_file: Optional[TextIO] = None


def _rotate() -> None:
    """verbaliox.log -> .1 -> .2 ...; the oldest is dropped. Call with _lock held."""
    global _file
    if _file is not None:
        _file.close()
    for i in range(KEEP_FILES - 1, 0, -1):
        older = LOG_FILE.with_name(f"{LOG_FILE.name}.{i}")
        newer = LOG_FILE if i == 1 else LOG_FILE.with_name(f"{LOG_FILE.name}.{i - 1}")
        try:
            if newer.exists():
                newer.replace(older)
        except OSError:
            pass
    _file = open(LOG_FILE, "a", encoding="utf-8", buffering=1)


class _Tee:
    """Writes to the log file and, when there is one, to the original stream."""

    def __init__(self, original: Optional[TextIO], label: str):
        self._original = original
        self._label = label
        self._at_line_start = True

    def write(self, text: str) -> int:
        if not text:
            return 0
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                pass
        with _lock:
            if _file is None:
                return len(text)
            out = []
            for part in text.splitlines(keepends=True):
                if self._at_line_start:
                    out.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self._label} ")
                out.append(part)
                self._at_line_start = part.endswith("\n")
            try:
                _file.write("".join(out))
                if _file.tell() > MAX_BYTES:
                    _rotate()
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass
        with _lock:
            if _file is not None:
                try:
                    _file.flush()
                except Exception:
                    pass

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"


def start_file_log() -> Path:
    """Starts copying stdout/stderr into the log file. Safe to call once per process."""
    global _file
    with _lock:
        if _file is not None:
            return LOG_FILE
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_BYTES:
            _rotate()
        else:
            _file = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        _file.write(f"\n===== Verbaliox started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(Python {sys.version.split()[0]}, {sys.executable}) =====\n")
    sys.stdout = _Tee(sys.__stdout__, "OUT")
    sys.stderr = _Tee(sys.__stderr__, "ERR")
    # Hard crashes (access violation in a native library) still leave a Python stack in the log
    try:
        faulthandler.enable(file=open(LOG_DIR / "crash.log", "a", encoding="utf-8"), all_threads=True)
    except Exception:
        pass
    return LOG_FILE
