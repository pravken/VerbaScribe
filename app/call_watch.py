"""
Recording calls by themselves: when Zoom, Teams, Telegram or another chosen app starts using the
microphone, a recording starts; when the app lets the microphone go, the recording stops and is
transcribed.

Windows keeps, per app, when it last started and stopped using the microphone (the privacy
"recently used" list: CapabilityAccessManager\\ConsentStore\\microphone in the registry; a start
newer than the last stop, or a stop time of 0, means "using it now"). Reading it needs no permission and catches calls in any of these apps,
whether they are classic programs or Store apps. The watcher polls it, and the page, which does the
recording, gets "call" events (app/events.py) to start and stop.

A call has to hold the microphone for START_DELAY_SEC before recording starts (a short voice message
is not a call), and a recording that the watcher started is stopped STOP_GRACE_SEC after the
microphone is let go (a mute or a device switch in the middle of a call does not end it).
"""

import sys
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional

from app import events
from app.database import get_all_settings

POLL_SEC = 2.0
START_DELAY_SEC = 5.0
STOP_GRACE_SEC = 15.0

# What identifies each app in the list: program file names, Store package name prefixes
CALL_APPS: Dict[str, Dict[str, object]] = {
    "zoom": {"name": "Zoom", "match": ["zoom.exe"]},
    "teams": {"name": "Microsoft Teams", "match": ["teams.exe", "ms-teams.exe", "msteams_"]},
    "telegram": {"name": "Telegram", "match": ["telegram.exe", "telegrammessengerllp.telegramdesktop"]},
    "discord": {"name": "Discord", "match": ["discord.exe"]},
    "skype": {"name": "Skype", "match": ["skype.exe", "microsoft.skypeapp"]},
    "whatsapp": {"name": "WhatsApp", "match": ["whatsapp.exe", "5319275a.whatsappdesktop"]},
}
DEFAULT_APPS = "zoom,teams,telegram"

_MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def apps_using_microphone() -> List[str]:
    """Lower-case names of what uses the microphone right now: the program file name
    ("zoom.exe") for classic programs, the package name for Store apps. Empty outside Windows."""
    if sys.platform != "win32":
        return []
    import winreg

    out: List[str] = []

    def in_use(key) -> bool:
        try:
            start = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
            stop = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
        except OSError:
            return False
        # In use: started and not stopped since. Older builds write 0 as the stop time while in
        # use; newer ones keep the previous stop time, which is then older than the start
        return bool(start) and (not stop or stop < start)

    def walk(path: str, name_of: Callable[[str], str]) -> None:
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        if in_use(key):
                            out.append(name_of(sub))
                except OSError:
                    continue

    walk(_MIC_KEY, lambda sub: sub.lower())  # Store apps: "MSTeams_8wekyb3d8bbwe"
    # Classic programs: the path with "#" for "\": "C:#Users#me#AppData#Roaming#Zoom#bin#Zoom.exe"
    walk(_MIC_KEY + r"\NonPackaged", lambda sub: sub.split("#")[-1].lower())
    # Some apps never update that list while they record (Telegram calls): their active recording
    # sessions show them
    try:
        out.extend(n for n in capturing_processes() if n not in out)
    except Exception as e:
        print(f"[calls] audio sessions: {e}", flush=True)
    return out


def _guid(text: str):
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    g = GUID()
    ctypes.memmove(g.data, uuid.UUID(text).bytes_le, 16)
    return g


def capturing_processes() -> List[str]:
    """Lower-case program file names with an active recording session on any microphone, from
    Windows Core Audio (what the microphone indicator itself shows). Unlike the registry list this
    also sees apps that do not report their use there (Telegram calls). Plain COM through ctypes."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    ole32, kernel32 = ctypes.windll.ole32, ctypes.windll.kernel32
    HRESULT = ctypes.c_long

    def method(obj, index, *argtypes):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])

    def release(obj):
        if obj:
            method(obj, 2)(obj)

    ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; already initialized is fine
    names: List[str] = []
    enum = ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(_guid("BCDE0395-E52F-467C-8E3D-C4579291692E")), None, 23,  # CLSCTX_ALL
                              ctypes.byref(_guid("A95664D2-9614-4F35-A746-DE8DB63617E6")), ctypes.byref(enum)) != 0:
        return names
    coll = ctypes.c_void_p()
    try:
        # IMMDeviceEnumerator::EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE)
        if method(enum, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(enum, 1, 1, ctypes.byref(coll)) != 0:
            return names
        count = wintypes.UINT()
        method(coll, 3, ctypes.POINTER(wintypes.UINT))(coll, ctypes.byref(count))
        for i in range(count.value):
            dev, mgr, sessions = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
            try:
                if method(coll, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(coll, i, ctypes.byref(dev)) != 0:
                    continue
                # IMMDevice::Activate(IID_IAudioSessionManager2)
                if method(dev, 3, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                        dev, ctypes.byref(_guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")), 23, None, ctypes.byref(mgr)) != 0:
                    continue
                if method(mgr, 5, ctypes.POINTER(ctypes.c_void_p))(mgr, ctypes.byref(sessions)) != 0:
                    continue
                n = ctypes.c_int()
                method(sessions, 3, ctypes.POINTER(ctypes.c_int))(sessions, ctypes.byref(n))
                for k in range(n.value):
                    ctl, ctl2 = ctypes.c_void_p(), ctypes.c_void_p()
                    try:
                        if method(sessions, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(sessions, k, ctypes.byref(ctl)) != 0:
                            continue
                        st = ctypes.c_int()
                        method(ctl, 3, ctypes.POINTER(ctypes.c_int))(ctl, ctypes.byref(st))
                        if st.value != 1:  # AudioSessionStateActive
                            continue
                        if method(ctl, 0, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                                ctl, ctypes.byref(_guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")), ctypes.byref(ctl2)) != 0:
                            continue
                        pid = wintypes.DWORD()
                        method(ctl2, 14, ctypes.POINTER(wintypes.DWORD))(ctl2, ctypes.byref(pid))
                        h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                        if h:
                            buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                                name = buf.value.replace("/", "\\").split("\\")[-1].lower()
                                if name not in names:
                                    names.append(name)
                            kernel32.CloseHandle(h)
                    finally:
                        release(ctl2)
                        release(ctl)
            finally:
                release(sessions)
                release(mgr)
                release(dev)
    finally:
        release(coll)
        release(enum)
    return names


def microphone_records() -> List[Dict[str, object]]:
    """Every app Windows lists for the microphone, newest first, with its raw start and stop times:
    shown in Settings to see what the registry actually holds while a call runs."""
    if sys.platform != "win32":
        return []
    import winreg

    out: List[Dict[str, object]] = []
    for path, nonpackaged in ((_MIC_KEY, False), (_MIC_KEY + r"\NonPackaged", True)):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError as e:
            out.append({"name": f"{'NonPackaged' if nonpackaged else 'microphone'}: {e}", "start": 0, "stop": 0})
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        vals = {}
                        for v in ("LastUsedTimeStart", "LastUsedTimeStop"):
                            try:
                                vals[v] = int(winreg.QueryValueEx(key, v)[0])
                            except OSError:
                                vals[v] = None
                except OSError:
                    continue
                name = sub.split("#")[-1] if nonpackaged else sub
                out.append({"name": name.lower(), "start": vals["LastUsedTimeStart"], "stop": vals["LastUsedTimeStop"]})
    out.sort(key=lambda r: -(r["start"] or 0))
    return out


def matching_app(in_use: List[str], watched: List[str]) -> Optional[str]:
    """The first watched app (a key of CALL_APPS) among what uses the microphone."""
    for key in watched:
        app = CALL_APPS.get(key)
        if not app:
            continue
        for name in in_use:
            if any(name == m or name.startswith(m) for m in app["match"]):  # type: ignore[union-attr]
                return key
    return None


def watched_apps(settings: Dict[str, str]) -> List[str]:
    return [a.strip() for a in (settings.get("call_apps") or DEFAULT_APPS).split(",") if a.strip() in CALL_APPS]


class CallWatcher:
    """The state machine, separate from the registry so it can be tested: feed it what uses the
    microphone with tick(); it publishes "call" events when a call starts and ends."""

    def __init__(self, publish: Callable[[Dict[str, object]], None] = events.publish):
        self.publish = publish
        self.candidate: Optional[str] = None   # a watched app that holds the microphone, not yet long enough
        self.candidate_since = 0.0
        self.active: Optional[str] = None      # the call a "started" event was sent for
        self.released_at: Optional[float] = None

    def tick(self, app: Optional[str], now: float) -> None:
        if app != self.candidate and not self.active:
            print(f"[calls] microphone: {CALL_APPS[app]['name'] if app else 'released'}", flush=True)
        if self.active:
            if app:
                self.released_at = None
            elif self.released_at is None:
                self.released_at = now
            elif now - self.released_at >= STOP_GRACE_SEC:
                print(f"[calls] call in {self.active} ended: stopping the recording", flush=True)
                self.publish({"type": "call", "state": "ended", "app": self.active,
                              "name": CALL_APPS[self.active]["name"]})
                self.active, self.released_at, self.candidate = None, None, None
            return
        if app != self.candidate:
            self.candidate, self.candidate_since = app, now
            return
        if app and now - self.candidate_since >= START_DELAY_SEC:
            self.active, self.released_at = app, None
            print(f"[calls] call in {app}: starting a recording", flush=True)
            self.publish({"type": "call", "state": "started", "app": app, "name": CALL_APPS[app]["name"]})


_watcher = CallWatcher()
_last_seen: List[List[str]] = [[]]
_started = False
_lock = threading.Lock()


def _run() -> None:
    while True:
        time.sleep(POLL_SEC)
        try:
            settings = get_all_settings()
            if (settings.get("call_auto_record") or "false").lower() != "true":
                _watcher.tick(None, time.time())  # switched off mid-call: the recording is ended as usual
                continue
            in_use = apps_using_microphone()
            if in_use != _last_seen[0]:  # every change of what holds the microphone, for diagnosing
                print(f"[calls] microphone in use by: {', '.join(in_use) or 'nobody'}", flush=True)
                _last_seen[0] = in_use
            _watcher.tick(matching_app(in_use, watched_apps(settings)), time.time())
        except Exception:
            traceback.print_exc()
            time.sleep(10)


def start() -> None:
    global _started
    if sys.platform != "win32":
        return
    with _lock:
        if not _started:
            threading.Thread(target=_run, daemon=True, name="call_watch").start()
            _started = True
            print("[calls] watcher started", flush=True)


def status() -> Dict[str, object]:
    """For the settings page: what uses the microphone right now, so it can be checked during a
    call that the app is seen at all."""
    try:
        mic = apps_using_microphone()
    except Exception as e:
        mic = [f"error: {e}"]
    try:
        records = microphone_records()
    except Exception as e:
        records = [{"name": f"error: {e}", "start": 0, "stop": 0}]
    return {"supported": sys.platform == "win32", "active": _watcher.active, "running": _started, "mic_now": mic,
            "records_count": len(records), "records": records[:3],
            "apps": [{"id": k, "name": v["name"]} for k, v in CALL_APPS.items()]}
