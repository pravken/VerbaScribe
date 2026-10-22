"""
The system-wide recording hotkey: parsing "Ctrl+Alt+R" into Win32 RegisterHotKey values, and what
the last registration gave (app/tray.py registers it on its thread; /api/hotkey shows the state).
Portable, so the API and the tests can use it on any system.
"""

from typing import Dict, Optional, Tuple

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEY_NAMES = {"space": 0x20, "pause": 0x13, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22}

# What the last registration of the hotkey gave, for the settings page
hotkey_state: Dict[str, object] = {"spec": "", "ok": None, "error": ""}
# The same for the dictation hotkey (app/dictation.py)
dictation_hotkey_state: Dict[str, object] = {"spec": "", "ok": None, "error": ""}


def parse_hotkey(spec: str) -> Optional[Tuple[int, int]]:
    """(modifiers, virtual key) of "Ctrl+Alt+R", "Ctrl+Shift+F9" and the like; None if it is empty
    or not valid. At least one of Ctrl, Alt or Win is required, so typing is never caught."""
    parts = [p.strip().lower() for p in (spec or "").replace(" ", "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MOD_NAMES:
            return None
        mods |= _MOD_NAMES[p]
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None
    key = parts[-1]
    if len(key) == 1 and (key.isalpha() and key.isascii() or key.isdigit()):
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    elif key in _KEY_NAMES:
        vk = _KEY_NAMES[key]
    else:
        return None
    return mods, vk
