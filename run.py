import argparse
import ctypes
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
ICO_PATH = BASE_DIR / "assets" / "icon.ico"
WINDOW_TITLE = "VerbaScribe"  # the launcher (build_exe.py) closes its splash when a window with this title appears
# uvicorn and the app are imported where they are used, inside the error handling of __main__: an
# incomplete .venv (setup interrupted, a blocked .pyd) then shows a message instead of nothing at all


def set_app_user_model_id() -> None:
    """
    Sets an explicit Windows AppUserModelID so the taskbar groups the window
    under VerbaScribe with our custom Willow icon rather than python.exe.
    """
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "VerbaScribe.Desktop.Studio.1"
            )
        except Exception:
            pass


def apply_native_window_icon_and_hide_console() -> None:
    """
    Hides the console window and applies assets/icon.ico directly to the native
    Win32 window handle (both ICON_SMALL and ICON_BIG) so the taskbar and titlebar
    display the Willow icon instead of the default Python icon.
    """
    if sys.platform == "win32":
        try:
            hwnd_console = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd_console:
                ctypes.windll.user32.ShowWindow(hwnd_console, 0)  # SW_HIDE = 0
        except Exception:
            pass

        if ICO_PATH.exists():
            try:
                user32 = ctypes.windll.user32
                LR_LOADFROMFILE = 0x00000010
                IMAGE_ICON = 1
                WM_SETICON = 0x0080
                ICON_SMALL = 0
                ICON_BIG = 1

                hicon_big = user32.LoadImageW(
                    0, str(ICO_PATH), IMAGE_ICON, 256, 256, LR_LOADFROMFILE
                )
                hicon_small = user32.LoadImageW(
                    0, str(ICO_PATH), IMAGE_ICON, 32, 32, LR_LOADFROMFILE
                )

                for _ in range(25):
                    hwnd = find_app_window(os.getpid())
                    if hwnd:
                        if hicon_big:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                        if hicon_small:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                        break
                    time.sleep(0.1)
            except Exception:
                pass


# Windows of other programs that can carry the title too: Explorer showing the program's folder
# (named VerbaScribe) is the usual one
_FOREIGN_WINDOW_CLASSES = {"CabinetWClass", "ExploreWClass"}


def find_app_window(pid: Optional[int] = None) -> int:
    """The window titled VerbaScribe (of process `pid` when given), never an Explorer window of a
    folder with that name; 0 when there is none."""
    if sys.platform != "win32":
        return 0
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = None
    name = ctypes.create_unicode_buffer(256)
    owner = wintypes.DWORD()
    while True:
        hwnd = user32.FindWindowExW(None, hwnd, None, WINDOW_TITLE)
        if not hwnd:
            return 0
        user32.GetClassNameW(hwnd, name, 256)
        if name.value in _FOREIGN_WINDOW_CLASSES:
            continue
        if pid is not None:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                continue
        return int(hwnd)


def is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def probe_server(host: str, port: int) -> bool:
    """True when the program on the port is this user's VerbaScribe: it accepts the session token
    from our data folder. Another program on the port (a dev server, another app) or VerbaScribe
    of another Windows user does not, and the window must not open their page."""
    import http.client

    token = read_session_token(timeout=0)
    if not token:
        return False
    try:
        conn = http.client.HTTPConnection(host, port, timeout=3)
        try:
            conn.request("GET", f"/?token={token}")
            resp = conn.getresponse()
            return resp.status == 303 and "vs_session" in (resp.getheader("set-cookie") or "")
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def choose_port(host: str, port: int, tries: int = 20) -> Tuple[int, bool]:
    """The port to use and whether our server already runs there. A port taken by another program
    is skipped for the next free one."""
    for p in range(port, port + tries):
        if not is_port_in_use(host, p):
            return p, False
        if probe_server(host, p):
            return p, True
        print(f"Port {p} is taken by another program")
    return port, False


def wait_for_server(host: str, port: int, timeout: float = 120.0) -> bool:
    # The first start after boot imports torch & co. from a cold disk: that can take a minute
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_port_in_use(host, port):
            return True
        time.sleep(0.1)
    return False


def read_session_token(timeout: float = 5.0) -> str:
    """The per-launch session token: ours when this process started the server, else the one the
    running server wrote (app.main)."""
    from app.paths import SESSION_TOKEN_FILE as token_file

    if os.environ.get("VERBASCRIBE_TOKEN"):
        return os.environ["VERBASCRIBE_TOKEN"]

    deadline = time.time() + timeout
    while True:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                return token
        except OSError:
            pass
        if time.time() >= deadline:
            return ""
        time.sleep(0.1)


def window_url(host: str, port: int) -> str:
    """The window's address: no token in it (it would stay in WebView2's history); the page signs
    in through WindowApi.session_token."""
    return f"http://{host}:{port}/"


def app_url(host: str, port: int) -> str:
    token = read_session_token()
    return f"http://{host}:{port}/?token={token}" if token else f"http://{host}:{port}/"


# Why the server thread ended, if it did (shown in the window instead of "connection refused")
SERVER_STATE = {"error": ""}


def start_server_in_thread(host: str, port: int) -> threading.Thread:
    import uvicorn

    from app.hardware import register_cuda_dlls

    # The token is chosen here and handed to the server, so the window never opens with a stale
    # token file left by the previous run (the server may still be starting)
    import secrets

    os.environ["VERBASCRIBE_TOKEN"] = secrets.token_urlsafe(32)
    register_cuda_dlls()
    config = uvicorn.Config("app.main:app", host=host, port=port, log_level="warning", access_log=False,
                            timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)

    def _run() -> None:
        import traceback

        try:
            server.run()
            if not server.started:
                SERVER_STATE["error"] = "Сервер остановился при запуске (подробности в журнале)"
        except BaseException as e:  # an import error, a blocked library file, a port in use (SystemExit)
            traceback.print_exc()
            SERVER_STATE["error"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_run, daemon=True, name="uvicorn_server")
    t.start()
    return t


# Shown at once while the server starts (the first start after installing can take minutes:
# antivirus checks every library file); replaced by the app as soon as the server answers
LOADING_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#141416;color:#F5F5F7;
font:14px "Segoe UI",system-ui,sans-serif}.box{max-width:640px;padding:32px;text-align:center}
.dot{width:10px;height:10px;border-radius:50%;background:#0A84FF;display:inline-block;margin:0 3px;animation:p 1.2s infinite ease-in-out}
.dot:nth-child(2){animation-delay:.2s}.dot:nth-child(3){animation-delay:.4s}@keyframes p{0%,100%{opacity:.25}50%{opacity:1}}
h1{font-size:18px;font-weight:600;margin:18px 0 8px}p{color:#98989D;line-height:1.5;margin:6px 0}
pre{text-align:left;white-space:pre-wrap;background:#242426;border:1px solid #3a3a3c;border-radius:10px;padding:12px;color:#FF9F0A;font-size:12px}
code{color:#F5F5F7}</style></head><body><div class="box" id="box">
<div><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
<h1>Запуск VerbaScribe…</h1><p id="hint">Первый запуск после установки может занять несколько минут:
Windows проверяет файлы библиотек.</p></div>
<script>
let t0 = Date.now();
setInterval(() => { const s = Math.round((Date.now() - t0) / 1000); if (s > 20) document.getElementById('hint').textContent =
  'Идёт запуск (' + s + ' с). Первый запуск после установки может занять несколько минут: Windows проверяет файлы библиотек.'; }, 1000);
function showError(text, log, sac) {
  document.getElementById('box').innerHTML = '<h1>Сервер VerbaScribe не запустился</h1><pre></pre>'
    + (sac ? '<p>Похоже, файл заблокировала функция Windows «Интеллектуальное управление приложениями». Выключите её: Безопасность Windows → Управление приложениями/браузером → Параметры интеллектуального управления приложениями → Выкл., затем запустите VerbaScribe снова.</p>' : '')
    + '<p>Журнал: <code></code></p>';
  document.querySelector('pre').textContent = text;
  document.querySelector('code').textContent = log;
}
</script></body></html>"""


def open_app_when_ready(window, host: str, port: int, log_path: str) -> None:
    """Replaces the loading screen with the app once the server answers, or shows why it did not
    start. No time limit: a slow first start is still a start."""
    import json

    while not SERVER_STATE["error"]:
        if is_port_in_use(host, port):
            window.load_url(window_url(host, port))
            return
        time.sleep(0.3)
    err = SERVER_STATE["error"]
    try:  # uvicorn reports an import error in the log and only then exits: show the end of the log
        tail = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        err = err + "\n\n" + "\n".join(tail)
    except OSError:
        pass
    # WinError 4551 / "Application Control policy": Smart App Control or WDAC blocked a file
    sac = "4551" in err or "Application Control" in err or "управления приложениями" in err
    window.evaluate_js(f"showError({json.dumps(err)}, {json.dumps(log_path)}, {json.dumps(sac)})")


class WindowApi:
    """Exposed to the page as window.pywebview.api: things the embedded browser cannot do itself."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self._fullscreen = False
        self._host, self._port = host, port

    def session_token(self) -> str:
        """The session token for the "locked" page, which signs in with it (POST /api/session)."""
        return read_session_token()

    def toggle_fullscreen(self) -> bool:
        """WebView2 inside pywebview ignores the Fullscreen API, so the native window is toggled."""
        import webview

        if webview.windows:
            webview.windows[0].toggle_fullscreen()
            self._fullscreen = not self._fullscreen
        return self._fullscreen

    def show_window(self) -> None:
        """Brings the window back from the tray or the taskbar, for a question the page must ask (the home
        page's «record this in-person meeting?»)."""
        import webview

        if webview.windows:
            try:
                webview.windows[0].show()
                webview.windows[0].restore()
            except Exception:
                pass
        activate_existing_window(timeout=2)


_instance_mutex = None


def acquire_single_instance() -> bool:
    """
    True if this is the only desktop window of VerbaScribe for this user. A named mutex is
    atomic, unlike checking the port: two quick double-clicks cannot both start a window.
    """
    global _instance_mutex
    if sys.platform != "win32":
        return True
    ERROR_ALREADY_EXISTS = 183
    # use_last_error: ctypes keeps the error of this very call (another call in between, even
    # inside ctypes, may reset GetLastError)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\VerbaScribe.Desktop.Window")
    if _instance_mutex and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
        return True
    # Our handle would keep the other instance's lock alive even after it ends
    if _instance_mutex:
        kernel32.CloseHandle(ctypes.c_void_p(_instance_mutex))
    _instance_mutex = None
    return False


def _pid_file() -> Path:
    from app.paths import DATA_DIR

    return DATA_DIR / "app.pid"


def write_pid_file() -> None:
    """The window's process and its program file: a stuck instance is only ended when the PID
    still belongs to that program (Windows reuses PIDs of ended processes)."""
    try:
        # The image of this process itself: the python.exe of a venv only starts the base interpreter
        image = _process_image(os.getpid()) or sys.executable
        _pid_file().write_text(f"{os.getpid()}\n{image}", encoding="utf-8")
    except OSError:
        pass


def remove_pid_file() -> None:
    try:
        lines = _pid_file().read_text(encoding="utf-8").split("\n")
        if lines[0].strip() == str(os.getpid()):
            _pid_file().unlink()
    except (OSError, IndexError):
        pass


def _process_image(pid: int) -> str:
    """The program file of a running process, '' if there is none or it cannot be read."""
    if sys.platform != "win32":
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        ok = kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(h))


def stuck_instance_pid() -> int:
    """The PID from app.pid when that process is still the VerbaScribe that wrote it, else 0."""
    try:
        lines = _pid_file().read_text(encoding="utf-8").split("\n")
        pid, exe = int(lines[0].strip()), (lines[1].strip() if len(lines) > 1 else "")
    except (OSError, ValueError):
        return 0
    if not exe or pid == os.getpid():
        return 0  # written by an older version without the program file: not safe to end
    image = _process_image(pid)
    return pid if image and os.path.normcase(os.path.abspath(image)) == os.path.normcase(os.path.abspath(exe)) else 0


# MB_TOPMOST | MB_SETFOREGROUND: the launcher's splash stays on top, a question must not hide under it
MB_ON_TOP = 0x40000 | 0x10000


def wait_for_other_instance(timeout: float = 15.0) -> str:
    """
    Another VerbaScribe holds the single-instance lock. Returns "activated" when its window was
    brought to the front, "acquired" when it ended meanwhile (e.g. it was just closed and is still
    shutting down) and this launch now owns the lock, or "timeout" when it neither shows a window
    nor ends.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if activate_existing_window(timeout=0.3):
            return "activated"
        if acquire_single_instance():
            return "acquired"
        time.sleep(0.2)
    return "timeout"


def recover_stuck_instance() -> bool:
    """
    The other instance holds the single-instance lock but shows no window (its window crashed and
    the process hangs). Offers to end it; True if it was ended and this launch may continue.
    """
    pid = stuck_instance_pid()
    MB_YESNO, MB_ICONWARNING, IDYES = 0x4, 0x30, 6
    if not pid:  # which process it is is not known for sure: ending a guess could hit another program
        ctypes.windll.user32.MessageBoxW(
            None,
            "VerbaScribe уже запущен, но его окно не отвечает.\n\n"
            "Завершите процесс python.exe программы VerbaScribe в Диспетчере задач или перезагрузите компьютер.",
            "VerbaScribe", MB_ICONWARNING | MB_ON_TOP)
        return False
    answer = ctypes.windll.user32.MessageBoxW(
        None,
        "VerbaScribe уже запущен, но его окно не отвечает.\n\n"
        "Завершить зависший процесс и открыть программу заново? Незавершённая обработка будет прервана.",
        "VerbaScribe", MB_YESNO | MB_ICONWARNING | MB_ON_TOP)
    if answer != IDYES:
        return False
    import subprocess

    print(f"Ending the stuck VerbaScribe process {pid}")
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(20):
        time.sleep(0.25)
        if acquire_single_instance():
            return True
    return False


def activate_existing_window(timeout: float = 30.0) -> bool:
    """Brings the already open VerbaScribe window to the front (it may still be starting)."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_app_window()
        if hwnd:
            if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):  # minimized or in the tray
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.25)
    return False


class TrayController:
    """
    The tray icon of the desktop window (app/tray.py): status from app/app_status.py, a
    notification when a job finishes while the window is not in front, menu commands passed to the
    page (trayCommand in app.js), and closing the window while something runs keeps the app in the
    tray instead of stopping the work.
    """

    def __init__(self, window) -> None:
        from app.tray import MF_DEFAULT, MF_GRAYED, Tray

        self.window = window
        self.quitting = False
        self._told_tray = False  # "the app stays in the tray" is said once per run
        self._status = {"state": "idle", "text": "", "recording": False, "queue": []}
        self._running: dict = {}
        self._mf_default, self._mf_grayed = MF_DEFAULT, MF_GRAYED
        self.tray = Tray(ICO_PATH, self.menu_items, self.on_command, self.show_window)
        window.events.closing += self.on_closing

    def start(self) -> None:
        self._apply_hotkey()
        started = ICO_PATH.exists() and self.tray.start()
        print(f"[tray] icon {'shown' if started and self.tray.hwnd else 'not shown'}")
        if started:
            threading.Thread(target=self._poll, daemon=True, name="tray_status").start()

    # ---- quiet mode: no notifications from the tray (switched in its menu or in Settings)
    def _quiet(self) -> bool:
        from app.database import get_all_settings

        return (get_all_settings().get("quiet_mode") or "false").lower() == "true"

    def _notify(self, title: str, text: str, warning: bool = False) -> None:
        if not self._quiet():
            self.tray.notify(title, text, warning=warning)

    def _toggle_quiet(self) -> None:
        from app.database import update_settings

        update_settings({"quiet_mode": "false" if self._quiet() else "true"})

    # ---- global hotkey: starts or stops a recording with the default preset, window or not
    def _apply_hotkey(self) -> None:
        from app.database import get_all_settings

        settings = get_all_settings()
        spec = (settings.get("record_hotkey") or "").strip()
        if spec != getattr(self, "_hotkey_spec", None):
            self._hotkey_spec = spec
            self.tray.set_hotkey(spec, "hotkey_record")
        dspec = (settings.get("dictation_hotkey") or "").strip()
        if dspec != getattr(self, "_dictation_spec", None):
            from app.tray import DICTATION_HOTKEY_ID

            self._dictation_spec = dspec
            self.tray.set_hotkey(dspec, "hotkey_dictation", slot=DICTATION_HOTKEY_ID)
        tspec = (settings.get("dictation_translate_hotkey") or "").strip()
        if tspec != getattr(self, "_translate_spec", None):
            from app.tray import TRANSLATE_HOTKEY_ID

            self._translate_spec = tspec
            self.tray.set_hotkey(tspec, "hotkey_dictation_translate", slot=TRANSLATE_HOTKEY_ID)

    def _hotkey_record(self) -> None:
        # The notice waits for the page to report the new state (_check_hotkey_result): starting can
        # fail (no microphone, the page still loading)
        self._hotkey_wait = (bool(self._status.get("recording")), time.time())
        self.window.evaluate_js("window.trayCommand && window.trayCommand('record', null)")

    def _check_hotkey_result(self, st: dict) -> None:
        wait = getattr(self, "_hotkey_wait", None)
        if not wait:
            return
        was, since = wait
        now = bool(st.get("recording"))
        key = self._hotkey_spec
        if now != was:
            self._hotkey_wait = None
            if now:
                self._notify("Запись начата", f"Чтобы остановить, нажмите {key}" if key else "")
            else:
                self._notify("Запись остановлена", "Запись сохранена и отправлена на расшифровку")
        elif time.time() - since > 10:
            self._hotkey_wait = None
            self._notify("Запись не остановилась" if was else "Запись не началась",
                         "Откройте окно VerbaScribe, чтобы увидеть причину", warning=True)

    def _warn_hotkey_failures(self) -> None:
        """A hotkey that could not be registered is said once, not only on the settings page."""
        from app.hotkey import dictation_hotkey_state, hotkey_state, translate_hotkey_state

        warned = getattr(self, "_hotkey_warned", set())
        self._hotkey_warned = warned
        for name, state in (("записи", hotkey_state), ("диктовки", dictation_hotkey_state),
                            ("диктовки с переводом", translate_hotkey_state)):
            if state.get("ok") is False:
                key = (name, state.get("spec"), state.get("error"))
                if key not in warned:
                    warned.add(key)
                    self._notify(f"Горячая клавиша {name} не работает",
                                 f"{state.get('spec')}: {state.get('error')}. Её можно сменить в Настройках → Запись",
                                 warning=True)

    # ---- status
    def _poll(self) -> None:
        from app.app_status import snapshot
        from app.database import get_meeting

        tick = 0
        while not self.quitting:
            tick += 1
            if tick % 300 == 0:  # every five minutes
                try:
                    self._update_in_background()
                except Exception as e:
                    print(f"[update] background install: {e}", file=sys.stderr)
            if tick % 5 == 0:
                try:
                    self._apply_hotkey()  # changed in Settings → Recording
                    self._warn_hotkey_failures()
                except Exception as e:
                    print(f"[tray] hotkey error: {e}", file=sys.stderr)
            try:
                st = snapshot()
                self._status = st
                self._check_hotkey_result(st)
                self.tray.set_status(st["state"], st["text"])
                running = {(q["id"], q["kind"]): q for q in st["queue"] if q.get("state") == "running"}
                for key, q in self._running.items():
                    if key not in {(x["id"], x["kind"]) for x in st["queue"]}:
                        self._job_finished(q, get_meeting(q["id"]))
                self._running = running
            except Exception as e:
                print(f"[tray] status error: {e}", file=sys.stderr)
            time.sleep(1.0)

    def _job_finished(self, q: dict, m) -> None:
        if not m or self._window_in_front():
            return
        from app.app_status import KIND_LABELS

        label = KIND_LABELS.get(q.get("kind"), "Задача")
        title = m.get("title") or ""
        status = m.get("status")
        if status == "failed":
            self._notify(f"{label}: ошибка", title, warning=True)
        elif status == "cancelled":
            return
        else:
            self._notify(f"{label} завершено", title)

    def _window_in_front(self) -> bool:
        user32 = ctypes.windll.user32
        hwnd = find_app_window(os.getpid())
        return bool(hwnd) and user32.GetForegroundWindow() == hwnd and bool(user32.IsWindowVisible(hwnd))

    # ---- menu
    def menu_items(self):
        st = self._status
        return [
            ("Открыть VerbaScribe", "open", self._mf_default),
            (None, None, 0),
            (st.get("text") or "Готово к работе", None, self._mf_grayed),
            *self._quick_record_item(st),
            *self._record_items(st),
            ("Импорт файлов…", "import", 0),
            *self._dictation_item(st),
            ("Открыть последнюю запись", "last", 0),
            ("Вопрос по медиатеке…", "ask", 0),
            (None, None, 0),
            ("Тихий режим (без уведомлений)", "quiet", 0x8 if self._quiet() else 0),  # MF_CHECKED
            ("Настройки…", "settings", 0),
            ("Открыть папку данных", "data", 0),
            (None, None, 0),
            ("Выход", "quit", 0),
        ]

    def _quick_record_item(self, st):
        """One click: records with the default preset without bringing the window up."""
        if st.get("recording"):
            return []
        key = getattr(self, "_hotkey_spec", "")
        return [("● Начать запись" + (f"\t{key}" if key else ""), "hotkey_record", 0)]

    def _dictation_item(self, st):
        """Dictation into the program with the focus, as by its hotkey (app/dictation.py)."""
        from app import dictation

        key = getattr(self, "_dictation_spec", "")
        if dictation.status().get("active"):
            return [("■ Закончить диктовку" + (f"\t{key}" if key else ""), "hotkey_dictation", 0)]
        return [("Диктовка" + (f"\t{key}" if key else ""), "hotkey_dictation", 0)]

    def _record_items(self, st):
        """One entry per recording preset (app.js BUILTIN_PRESETS + the user's own)."""
        if st.get("recording"):
            key = getattr(self, "_hotkey_spec", "")
            return [("■ Остановить запись" + (f"\t{key}" if key else ""), "record", 0)]
        import json

        from app.database import get_all_settings

        settings = get_all_settings()
        presets = [("call", "Созвон"), ("note", "Голосовая заметка"), ("system", "Звук компьютера")]
        try:
            presets += [(p["id"], p["name"]) for p in json.loads(settings.get("record_presets") or "[]")
                        if isinstance(p, dict) and p.get("id") and p.get("name")]
        except ValueError:
            pass
        default = settings.get("record_default_preset") or "call"
        return [(f"Запись: {name}" + ("  ★" if pid == default else ""), f"record:{pid}", 0)
                for pid, name in presets[:12]]

    def on_command(self, cmd: str) -> None:
        if cmd == "quiet":
            self._toggle_quiet()
            return
        if cmd == "hotkey_record":
            self._hotkey_record()
            return
        if cmd == "hotkey_dictation":
            from app import dictation

            # The text is typed where the cursor is; the notice is shown only when it starts
            if dictation.toggle(notify=self._notify) == "started":
                self._notify("Диктовка", f"Говорите. Чтобы вставить текст, нажмите {self._dictation_spec} ещё раз")
            return
        if cmd == "hotkey_dictation_translate":
            from app import dictation
            from app.translation import TEXT_LANGUAGES

            if dictation.toggle(notify=self._notify, translate=True) == "started":
                lang = dictation.translate_target()
                self._notify("Диктовка с переводом",
                             f"Говорите. Текст будет переведён ({TEXT_LANGUAGES.get(lang, lang)}). "
                             f"Чтобы вставить его, нажмите {self._translate_spec} ещё раз")
            return
        if cmd.startswith("record:"):
            self.show_window()
            import json

            preset = json.dumps(cmd.split(":", 1)[1])
            self.window.evaluate_js(f"window.trayCommand && window.trayCommand('record', {preset})")
            return
        if cmd == "open":
            self.show_window()
        elif cmd == "last":
            self.show_window()
            self.window.evaluate_js("window.openLastRecording && window.openLastRecording()")
        elif cmd in ("record", "import", "ask", "settings"):
            self.show_window()
            self.window.evaluate_js(f"window.trayCommand && window.trayCommand('{cmd}')")
        elif cmd == "data":
            from app.paths import DATA_DIR

            os.startfile(str(DATA_DIR))
        elif cmd == "quit":
            self.quit()

    def show_window(self) -> None:
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass
        activate_existing_window(timeout=2)

    def quit(self) -> None:
        st = self._status
        if st.get("state") != "idle":
            MB_YESNO, MB_ICONWARNING, IDYES = 0x4, 0x30, 6
            what = "Идёт запись" if st.get("recording") else "Идёт обработка"
            answer = ctypes.windll.user32.MessageBoxW(
                None, f"{what}. Выйти из VerbaScribe? Незавершённая работа будет прервана.", "VerbaScribe",
                MB_YESNO | MB_ICONWARNING | MB_ON_TOP)
            if answer != IDYES:
                return
        self.quitting = True
        self.window.destroy()

    def on_closing(self) -> bool:
        from app.database import get_all_settings

        to_tray = (get_all_settings().get("close_to_tray") or "false").lower() == "true"
        if self.quitting or (self._status.get("state") == "idle" and not to_tray):
            self.quitting = True
            return True
        # Something runs, or the app stays in the tray by choice (Settings → General): the window hides
        threading.Timer(0.05, self.window.hide).start()
        if self._status.get("state") != "idle":
            self._notify("VerbaScribe работает в фоне",
                         "Запись или обработка продолжается. Значок в трее — открыть окно или выйти.")
        elif not self._told_tray:
            self._told_tray = True
            self._notify("VerbaScribe в трее", "Программа продолжает работать. Значок в трее — открыть окно или выйти.")
        return False

    def _update_in_background(self) -> None:
        """The app now rarely closes (it stays in the tray): an update it downloaded installs while the
        window is hidden and nothing runs, and the app comes back in the tray."""
        from app import updater

        if self.quitting or self._status.get("state") != "idle" or not updater.ready_to_install():
            return
        hwnd = find_app_window(os.getpid())
        if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
            return  # someone works in the window: on its closing, as before
        print("[update] installing the downloaded update in the background")
        try:
            updater.launch_installer(restart=True, minimized=True)
        except Exception as e:
            print(f"[update] not started: {e}", file=sys.stderr)
            return
        global _update_launched
        _update_launched = True
        self.quitting = True
        self.window.destroy()

    def stop(self) -> None:
        self.quitting = True
        self.tray.stop()
        time.sleep(0.3)  # lets the tray thread remove the icon before the process ends


def grant_media_to_app(window, host: str, port: int) -> None:
    """
    The app's own page may use the microphone (and screen capture as a fallback) without asking
    every time: WebView2's prompt shows a bare "http://127.0.0.1:8765 wants to use your
    microphones". Only this origin is granted; anything else still gets the normal prompt.
    """
    origin = f"http://{host}:{port}/"
    done = {"ok": False}

    def _attach() -> None:
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState

        def _on_permission(sender, args) -> None:
            try:
                if str(args.Uri).startswith(origin):
                    args.State = CoreWebView2PermissionState.Allow
            except Exception as e:
                print(f"[permissions] {e}", file=sys.stderr)

        core = window.native.browser.webview.CoreWebView2
        core.PermissionRequested += _on_permission
        done["handler"] = _on_permission  # keeps the delegate alive
        done["ok"] = True

    def _on_loaded() -> None:
        if done["ok"]:
            return
        try:
            from System import Action

            window.native.Invoke(Action(_attach))  # WebView2 is used on its UI thread only
        except Exception as e:
            print(f"[permissions] not set: {e}", file=sys.stderr)

    window.events.loaded += _on_loaded


_update_launched = False


def _stop_work() -> None:
    """Before the process ends: recordings finished, work processes ended (app/shutdown.py)."""
    remove_pid_file()
    try:
        from app import shutdown

        shutdown.stop_all()
    except Exception as e:
        print(f"[exit] {e}", file=sys.stderr)


def _install_update_on_exit() -> None:
    """A downloaded update (automatic updates on) is installed when the app closes."""
    global _update_launched
    if _update_launched:
        return
    _update_launched = True
    try:
        from app import updater

        if updater.install_on_exit():
            print("[update] installing the downloaded update on exit")
    except Exception as e:
        print(f"[update] not installed on exit: {e}", file=sys.stderr)


def show_error_box(text: str) -> None:
    # --headless runs without a window, maybe with nobody at the screen: a message box would wait forever
    if sys.platform == "win32" and "--headless" not in sys.argv:
        try:
            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(None, text, "VerbaScribe", MB_ICONERROR | MB_ON_TOP)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def main() -> None:
    import atexit

    import uvicorn

    atexit.register(_stop_work)  # the browser and headless modes end through Python's own exit

    from app.hardware import register_cuda_dlls

    set_app_user_model_id()

    parser = argparse.ArgumentParser(description="VerbaScribe Desktop Studio")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--browser", action="store_true", help="Open in external web browser")
    parser.add_argument("--headless", action="store_true", help="Run server without GUI window")
    # Started with Windows (app/autostart.py): in the tray, the window shown from the tray icon
    parser.add_argument("--minimized", action="store_true", help="Start in the tray without showing the window")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        # Allow the explicitly chosen host in the server's Host header check
        os.environ["VERBASCRIBE_ALLOWED_HOSTS"] = args.host
    args.port, already_running = choose_port(args.host, args.port)

    if args.headless:
        if not already_running:
            register_cuda_dlls()
            threading.Thread(
                target=lambda: wait_for_server(args.host, args.port)
                and print(f"VerbaScribe: {app_url(args.host, args.port)}", flush=True),
                daemon=True,
            ).start()
            uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info", access_log=False,  # the address holds the session token
                timeout_graceful_shutdown=3)
        return

    if not args.browser:
        if not acquire_single_instance():
            # A second double-click shows the window that is already open instead of a second copy;
            # an instance that is just closing is waited for
            outcome = wait_for_other_instance()
            print(f"Another VerbaScribe instance: {outcome}")
            if outcome == "activated" or (outcome == "timeout" and not recover_stuck_instance()):
                return
        # An installation made on Python 3.10 moves its environment to 3.11 once (app/envmove.py):
        # that runs outside this environment, so the app quits and is started again by it
        from app import envmove
        from app.paths import APP_DIR, DATA_DIR

        if envmove.needed(APP_DIR, DATA_DIR) and envmove.launch(APP_DIR, DATA_DIR):
            return
        envmove.cleanup(APP_DIR, DATA_DIR)
        write_pid_file()
        # A previous instance may still be releasing the port for a moment
        for _ in range(50):
            if not is_port_in_use(args.host, args.port) or not probe_server(args.host, args.port):
                break
            time.sleep(0.2)
        args.port, already_running = choose_port(args.host, args.port)
        server_thread = None
        try:
            import webview

            if not already_running:
                print("Starting VerbaScribe local AI server...")
                server_thread = start_server_in_thread(args.host, args.port)

            webview.settings["ALLOW_DOWNLOADS"] = True
            # The app's page once the server answers; until then a loading screen (see open_app_when_ready)
            window = webview.create_window(
                title=WINDOW_TITLE,
                url=window_url(args.host, args.port) if already_running else None,
                html=None if already_running else LOADING_HTML,
                width=1460,
                height=900,
                min_size=(1100, 700),
                background_color="#141416",
                text_select=True,
                js_api=WindowApi(args.host, args.port),
                hidden=args.minimized,
            )
            try:
                from app import autostart

                autostart.sync()  # the Run key follows the setting and this program folder
            except Exception as e:
                print(f"[autostart] {e}", file=sys.stderr)
            tray = None
            try:
                tray = TrayController(window)
                tray.start()
                from app import updater

                updater.exit_hooks.append(tray.stop)
                updater.exit_hooks.append(_stop_work)
            except Exception as e:  # the app works without the tray icon
                print(f"[tray] not available: {e}", file=sys.stderr)

            def _exit_soon() -> None:
                # Closing the window while WebView2 is still loading can leave webview.start()
                # hanging: the process would keep the single-instance lock without a window
                def _hard_exit() -> None:
                    time.sleep(2.0)
                    print("Window closed: ending the process")
                    if tray is not None:
                        tray.stop()
                    _stop_work()
                    _install_update_on_exit()
                    os._exit(0)

                threading.Thread(target=_hard_exit, daemon=True).start()

            window.events.closed += _exit_soon
            grant_media_to_app(window, args.host, args.port)
            icon_arg = str(ICO_PATH) if ICO_PATH.exists() else None
            # A persistent profile in the data folder: the page's own saved preferences (volume,
            # last tabs, ...) survive a restart; private mode (pywebview's default) forgets them
            from app.paths import DATA_DIR

            from app.applog import LOG_FILE

            def _on_gui_ready() -> None:
                if not already_running:
                    threading.Thread(target=open_app_when_ready, args=(window, args.host, args.port, str(LOG_FILE)),
                                     daemon=True, name="open_app").start()
                apply_native_window_icon_and_hide_console()

            webview.start(func=_on_gui_ready, icon=icon_arg,
                          private_mode=False, storage_path=str(DATA_DIR / "webview"))
            if tray is not None:
                tray.stop()
            _stop_work()
            _install_update_on_exit()
            os._exit(0)
        except Exception as e:
            print(f"[!] Native window fallback to browser ({e})...")
        if server_thread is not None:
            # The server already runs in this process: a second one would only fail on the busy port
            wait_for_server(args.host, args.port)
            webbrowser.open(app_url(args.host, args.port))
            server_thread.join()
            return

    # Fallback: regular browser mode
    if already_running:
        webbrowser.open(app_url(args.host, args.port))
        return

    def _open_browser():
        wait_for_server(args.host, args.port)
        webbrowser.open(app_url(args.host, args.port))

    threading.Thread(target=_open_browser, daemon=True).start()
    register_cuda_dlls()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info", access_log=False,  # the address holds the session token
                timeout_graceful_shutdown=3)


def _startup_failed(log_file) -> None:
    """Shows why the app did not start; without the log (the data folder unusable) it goes to %TEMP%."""
    import tempfile
    import traceback

    text = traceback.format_exc()
    if log_file is None:
        log_file = Path(tempfile.gettempdir()) / "verbascribe-start-error.log"
        try:
            log_file.write_text(text, encoding="utf-8")
        except OSError:
            pass
    else:
        print(text, file=sys.stderr)
    hint = ("\n\nЕсли установка прерывалась, запустите setup.bat ещё раз." if isinstance(sys.exc_info()[1], ImportError)
            else "")
    show_error_box(
        "Не удалось запустить VerbaScribe.\n\n"
        f"Подробности записаны в журнал:\n{log_file}{hint}\n\n"
        "VerbaScribe could not start. Details are in the log file above."
    )


if __name__ == "__main__":
    LOG_FILE = None
    try:
        from app.applog import LOG_FILE, start_file_log

        if "--headless" not in sys.argv:
            start_file_log()
        main()
    except SystemExit:
        raise
    except BaseException:
        _startup_failed(LOG_FILE)
        sys.exit(1)  # the launcher (build_exe.py) shows nothing more for exit code 1
