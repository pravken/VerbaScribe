"""
uv and pip for installing packages into a Python environment. setup.bat makes .venv with uv, which
puts no pip in it, so pip is bootstrapped with ensurepip when uv is not found.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_uv() -> Optional[str]:
    """uv on PATH or where its installers put it (the app may start without the updated PATH)."""
    found = shutil.which("uv")
    if found:
        return found
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for c in (home / ".local" / "bin" / "uv.exe", home / ".cargo" / "bin" / "uv.exe",
              Path(os.environ.get("LOCALAPPDATA") or home) / "Microsoft" / "WinGet" / "Links" / "uv.exe"):
        if c.is_file():
            return str(c)
    return None


def _ok(cmd) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=300).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_pip(python: str) -> bool:
    """True when `python -m pip` works, installing pip with ensurepip if the environment has none."""
    if _ok([python, "-m", "pip", "--version"]):
        return True
    _ok([python, "-m", "ensurepip", "--upgrade"])
    return _ok([python, "-m", "pip", "--version"])
