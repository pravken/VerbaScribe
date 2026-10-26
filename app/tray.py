"""
Notification-area (tray) icon of the desktop app, in plain Win32 through ctypes: no extra packages.

The icon shows what the app is doing (a red dot while recording, a blue one while a job runs),
the tooltip says what exactly, finished jobs raise a notification, and the right-click menu sends
commands to the window. Runs its own message loop in a background thread.
"""

import ctypes
import struct
import sys
import threading
import zlib
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from app.hotkey import MOD_NOREPEAT, altgr_clash, dictation_hotkey_state, hotkey_state, parse_hotkey, translate_hotkey_state

user32 = ctypes.windll.user32 if sys.platform == "win32" else None
shell32 = ctypes.windll.shell32 if sys.platform == "win32" else None
gdi32 = ctypes.windll.gdi32 if sys.platform == "win32" else None
kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_USER = 0x0400
WM_APP_TRAY = WM_USER + 20
WM_APP_UPDATE = WM_USER + 21
WM_APP_HOTKEY = WM_USER + 22
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
DICTATION_HOTKEY_ID = 2
TRANSLATE_HOTKEY_ID = 3
_HOTKEY_STATES = {HOTKEY_ID: hotkey_state, DICTATION_HOTKEY_ID: dictation_hotkey_state,
                  TRANSLATE_HOTKEY_ID: translate_hotkey_state}
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_DEFAULT = 0x0, 0x800, 0x1, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _setup_prototypes() -> None:
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, wintypes.LPVOID]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    user32.CreateIconIndirect.restype = wintypes.HICON
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


# ------------------------------------------------------------------ icons
def _png_rgba(png: bytes) -> Tuple[int, int, bytearray]:
    """Decodes the 8-bit RGBA, unfiltered PNGs that build_exe.py writes into assets/icon.ico."""
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(png):
        length, ctype = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
        elif ctype == b"IDAT":
            idat += data
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    pixels = bytearray()
    for y in range(height):
        row = raw[y * stride:(y + 1) * stride]
        if row[0] != 0:
            raise ValueError("unsupported PNG filter")
        pixels += row[1:]
    return width, height, pixels


def _base_pixels(ico_path: Path, size: int) -> Tuple[int, bytearray]:
    data = ico_path.read_bytes()
    count = struct.unpack("<H", data[4:6])[0]
    best = None
    for i in range(count):
        w, h, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        w = w or 256
        if best is None or abs(w - size) < abs(best[0] - size):
            best = (w, data[offset:offset + length])
    w, _, pixels = _png_rgba(best[1])
    return w, pixels


# Status dot colours (RGB)
DOTS = {"busy": (10, 132, 255), "recording": (255, 59, 48), "attention": (255, 159, 10)}


def _icon_with_dot(size: int, pixels: bytearray, dot: Optional[Tuple[int, int, int]]) -> int:
    px = bytearray(pixels)
    if dot:
        r = size * 0.22
        cx = cy = size - r - 0.5
        for y in range(size):
            for x in range(size):
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                i = (y * size + x) * 4
                if d <= r - 1.0:
                    px[i:i + 4] = bytes((*dot, 255))
                elif d <= r + 0.6:  # white ring separates the dot from the picture
                    px[i:i + 4] = bytes((255, 255, 255, 255))
    # RGBA top-down -> premultiplied BGRA for a 32-bit top-down DIB
    bgra = bytearray(len(px))
    for i in range(0, len(px), 4):
        r_, g_, b_, a_ = px[i], px[i + 1], px[i + 2], px[i + 3]
        bgra[i:i + 4] = bytes((b_ * a_ // 255, g_ * a_ // 255, r_ * a_ // 255, a_))
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    color = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, bytes(bgra), len(bgra))
    mask = gdi32.CreateBitmap(size, size, 1, 1, None)
    info = ICONINFO(True, 0, 0, mask, color)
    hicon = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(color)
    gdi32.DeleteObject(mask)
    return hicon


# ------------------------------------------------------------------ tray
class Tray:
    """
    menu_items: callable returning [(label, command_or_None, flags)]; label None = separator.
    on_command(command) is called on the tray thread; on_open() on a left click.
    """

    def __init__(self, ico_path: Path, menu_items: Callable[[], List[Tuple[Optional[str], Optional[str], int]]],
                 on_command: Callable[[str], None], on_open: Callable[[], None]):
        self.ico_path = ico_path
        self.menu_items = menu_items
        self.on_command = on_command
        self.on_open = on_open
        self.hwnd = None
        self.icons: Dict[str, int] = {}
        self.state = "idle"
        self.tip = "VerbaScribe"
        self._pending: Optional[Tuple[str, str, int]] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._wndproc = WNDPROC(self._proc)  # kept alive for the lifetime of the window

    # ---- public, any thread
    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        threading.Thread(target=self._run, daemon=True, name="tray").start()
        return self._ready.wait(5)

    def set_status(self, state: str, tip: str) -> None:
        with self._lock:
            changed = state != self.state or tip != self.tip
            self.state, self.tip = state, tip
        if changed and self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_UPDATE, 0, 0)

    def notify(self, title: str, text: str, warning: bool = False) -> None:
        with self._lock:
            self._pending = (title, text, NIIF_WARNING if warning else NIIF_INFO)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_UPDATE, 1, 0)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def set_hotkey(self, spec: str, command: str, slot: int = HOTKEY_ID) -> None:
        """Registers a system-wide hotkey that sends `command` (replaces the previous one in this
        slot: recording or dictation); an empty spec removes it. Registration happens on the tray
        thread, which owns the window."""
        with self._lock:
            if not hasattr(self, "_hotkeys"):
                self._hotkeys = {}
            self._hotkeys[slot] = (spec, command)
            self._hotkey = True
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_HOTKEY, 0, 0)

    def _register_hotkey(self) -> None:
        with self._lock:
            slots = dict(getattr(self, "_hotkeys", {}))
        self._hotkey_commands = {}
        for slot, (spec, command) in slots.items():
            state = _HOTKEY_STATES.get(slot, hotkey_state)
            user32.UnregisterHotKey(self.hwnd, slot)
            self._hotkey_commands[slot] = command
            state.update(spec=spec, ok=None, error="")
            if not spec:
                continue
            parsed = parse_hotkey(spec)
            if not parsed:
                state.update(ok=False, error="Неверное сочетание клавиш")
                continue
            typed = altgr_clash(spec)
            if typed:
                state.update(ok=False, error=f"На раскладке клавиатуры это сочетание (AltGr) печатает «{typed}» — выберите другое")
                continue
            if user32.RegisterHotKey(self.hwnd, slot, parsed[0] | MOD_NOREPEAT, parsed[1]):
                state.update(ok=True)
            else:
                state.update(ok=False, error="Сочетание уже занято другой программой")

    # ---- tray thread
    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self.icons.get(self.state) or self.icons.get("idle")
        nid.szTip = ("VerbaScribe — " + self.tip)[:127]
        return nid

    def _add_icon(self) -> None:
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)))

    def _run(self) -> None:
        _setup_prototypes()
        size = 32
        try:
            w, pixels = _base_pixels(self.ico_path, size)
            self.icons = {"idle": _icon_with_dot(w, pixels, None)}
            for name, rgb in DOTS.items():
                self.icons[name] = _icon_with_dot(w, pixels, rgb)
        except Exception as e:
            print(f"[tray] icon error: {e}", file=sys.stderr)
            self._ready.set()
            return
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "VerbaScribeTrayWindow"
        user32.RegisterClassW(ctypes.byref(wc))
        # A hidden top-level window (not message-only), so it also gets "TaskbarCreated"
        self.hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "VerbaScribe tray", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        if not self.hwnd:
            print(f"[tray] window not created (error {kernel32.GetLastError()})", file=sys.stderr)
        self._add_icon()
        if getattr(self, "_hotkey", None):
            self._register_hotkey()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        commands: Dict[int, str] = {}
        for idx, (label, cmd, flags) in enumerate(self.menu_items(), start=1):
            if label is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            user32.AppendMenuW(menu, MF_STRING | flags, idx, label)
            if cmd:
                commands[idx] = cmd
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)  # otherwise the menu does not close on an outside click
        chosen = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                       pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if chosen in commands:
            try:
                self.on_command(commands[chosen])
            except Exception as e:
                print(f"[tray] command error: {e}", file=sys.stderr)

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                try:
                    self.on_open()
                except Exception as e:
                    print(f"[tray] open error: {e}", file=sys.stderr)
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == WM_APP_HOTKEY:
            self._register_hotkey()
            return 0
        if msg == WM_HOTKEY and wparam in getattr(self, "_hotkey_commands", {}):
            try:
                self.on_command(self._hotkey_commands[wparam])
            except Exception as e:
                print(f"[tray] hotkey error: {e}", file=sys.stderr)
            return 0
        if msg == WM_APP_UPDATE:
            if wparam == 1:
                with self._lock:
                    pending, self._pending = self._pending, None
                if pending:
                    nid = self._nid(NIF_INFO)
                    nid.szInfoTitle = pending[0][:63]
                    nid.szInfo = pending[1][:255]
                    nid.dwInfoFlags = pending[2]
                    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            else:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid(NIF_ICON | NIF_TIP)))
            return 0
        if msg == getattr(self, "_taskbar_created", -1):
            self._add_icon()  # Explorer restarted: the icon has to be added again
            return 0
        if msg == WM_CLOSE:
            user32.UnregisterHotKey(hwnd, HOTKEY_ID)
            user32.UnregisterHotKey(hwnd, DICTATION_HOTKEY_ID)
            user32.UnregisterHotKey(hwnd, TRANSLATE_HOTKEY_ID)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(0)))
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            for h in self.icons.values():
                user32.DestroyIcon(h)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
