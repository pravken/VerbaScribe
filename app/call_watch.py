"""
Recording calls by themselves: when Zoom, Teams, Telegram or another chosen app starts using the
microphone, a recording starts; when the app lets the microphone go, the recording stops and is
transcribed.

Windows keeps, per app, when it last started and stopped using the microphone (the privacy
"recently used" list: CapabilityAccessManager\\ConsentStore\\microphone in the registry; a stop time
of 0 means "using it now"). Reading it needs no permission and catches calls in any of these apps,
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
        return bool(start) and not stop

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
        if self.active:
            if app:
                self.released_at = None
            elif self.released_at is None:
                self.released_at = now
            elif now - self.released_at >= STOP_GRACE_SEC:
                self.publish({"type": "call", "state": "ended", "app": self.active,
                              "name": CALL_APPS[self.active]["name"]})
                self.active, self.released_at, self.candidate = None, None, None
            return
        if app != self.candidate:
            self.candidate, self.candidate_since = app, now
            return
        if app and now - self.candidate_since >= START_DELAY_SEC:
            self.active, self.released_at = app, None
            self.publish({"type": "call", "state": "started", "app": app, "name": CALL_APPS[app]["name"]})


_watcher = CallWatcher()
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
            _watcher.tick(matching_app(apps_using_microphone(), watched_apps(settings)), time.time())
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


def status() -> Dict[str, object]:
    return {"supported": sys.platform == "win32", "active": _watcher.active,
            "apps": [{"id": k, "name": v["name"]} for k, v in CALL_APPS.items()]}
