"""
The system-wide recording hotkey: parsing "Ctrl+Alt+R" into Win32 RegisterHotKey values, and what
the last registration gave (app/tray.py registers it on its thread; /api/hotkey shows the state).
Portable, so the API and the tests can use it on any system.
"""

import sys
from typing import Callable, Dict, Optional, Tuple

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEY_NAMES = {"space": 0x20, "pause": 0x13, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22}

# What the last registration of the hotkey gave, for the settings page
hotkey_state: Dict[str, object] = {"spec": "", "ok": None, "error": ""}
# The same for the dictation hotkey (app/dictation.py)
dictation_hotkey_state: Dict[str, object] = {"spec": "", "ok": None, "error": ""}
# And for the dictation with translation
translate_hotkey_state: Dict[str, object] = {"spec": "", "ok": None, "error": ""}


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


VK_SHIFT, VK_CONTROL, VK_MENU, VK_LCONTROL, VK_RMENU = 0x10, 0x11, 0x12, 0xA2, 0xA5


def _typed_with_altgr(mods: int, vk: int) -> str:
    """What Ctrl+Alt+key (+Shift) types on the installed keyboard layouts, Windows only. To Windows
    AltGr is Ctrl+Alt: on a Polish, Czech or German layout such a hotkey takes a letter or a symbol
    away from typing in every program."""
    if sys.platform != "win32":
        return ""
    from ctypes import byref, c_ubyte, c_void_p, create_unicode_buffer, windll

    user32 = windll.user32
    n = user32.GetKeyboardLayoutList(0, None)
    layouts = (c_void_p * max(n, 1))()
    n = user32.GetKeyboardLayoutList(n, layouts)
    keys = (c_ubyte * 256)()
    for k in (VK_CONTROL, VK_MENU, VK_LCONTROL, VK_RMENU) + ((VK_SHIFT,) if mods & MOD_SHIFT else ()):
        keys[k] = 0x80
    buf = create_unicode_buffer(8)
    for hkl in layouts[:n]:
        scan = user32.MapVirtualKeyExW(vk, 0, c_void_p(hkl))
        # flag 0x4: leave the keyboard state (dead keys) of the thread as it was
        got = user32.ToUnicodeEx(vk, scan, byref(keys), buf, len(buf), 0x4, c_void_p(hkl))
        if got < 0:
            return "´"  # a dead key (an accent) on that layout
        if got > 0 and buf.value[:got].strip() and buf.value[:got].isprintable():
            return buf.value[:got]
    return ""


def altgr_clash(spec: str, typed: Callable[[int, int], str] = _typed_with_altgr) -> str:
    """The character the hotkey would take away from typing (see _typed_with_altgr), '' if none."""
    parsed = parse_hotkey(spec)
    if not parsed:
        return ""
    mods, vk = parsed
    if mods & MOD_CONTROL and mods & MOD_ALT and not mods & MOD_WIN:
        try:
            return typed(mods, vk)
        except Exception:  # an unusual layout must not stop the hotkey
            return ""
    return ""
