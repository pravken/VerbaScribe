"""
Start with Windows (Settings → General): the per-user Run key of the registry starts the app in
the tray, without a window (run.py --minimized), so automatic recording from the calendar, the
calls it notices and the hotkeys work from the moment the user signs in. pythonw.exe of the app's
environment starts it, without the launcher's splash and without a console.

The value names this program folder: a folder moved or copied elsewhere is set again at the next
start (sync, called by run.py).
"""

import sys
from pathlib import Path

from app.paths import APP_DIR

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VerbaScribe"


def command() -> str:
    pythonw = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{APP_DIR / "run.py"}" --minimized'


def current() -> str:
    """The command the Run key holds for VerbaScribe ("" when none, or not on Windows)."""
    if sys.platform != "win32":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, VALUE_NAME)[0])
    except OSError:
        return ""


def set_enabled(on: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def sync() -> None:
    """The Run key as the setting says, pointing at this folder (called at every start)."""
    from app.database import get_all_settings

    want = (get_all_settings().get("start_with_windows") or "false").lower() == "true"
    try:
        if want and current() != command():
            set_enabled(True)
        elif not want and current():
            set_enabled(False)
    except OSError as e:
        print(f"[autostart] not changed: {e}", file=sys.stderr)
