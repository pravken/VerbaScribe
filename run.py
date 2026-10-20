import argparse
import ctypes
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ICO_PATH = BASE_DIR / "assets" / "icon.ico"
WINDOW_TITLE = "VerbaScribe — AI Speech & Media Studio"

import uvicorn
from app.hardware import register_cuda_dlls


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
                    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
                    if hwnd:
                        if hicon_big:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                        if hicon_small:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                        break
                    time.sleep(0.1)
            except Exception:
                pass


def is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


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
    while time.time() < deadline:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                return token
        except OSError:
            pass
        time.sleep(0.1)
    return ""


def app_url(host: str, port: int) -> str:
    token = read_session_token()
    return f"http://{host}:{port}/?token={token}" if token else f"http://{host}:{port}/"


def start_server_in_thread(host: str, port: int) -> threading.Thread:
    # The token is chosen here and handed to the server, so the window never opens with a stale
    # token file left by the previous run (the server may still be starting)
    import secrets

    os.environ["VERBASCRIBE_TOKEN"] = secrets.token_urlsafe(32)
    register_cuda_dlls()
    config = uvicorn.Config("app.main:app", host=host, port=port, log_level="warning", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="uvicorn_server")
    t.start()
    return t


class WindowApi:
    """Exposed to the page as window.pywebview.api: things the embedded browser cannot do itself."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self._fullscreen = False
        self._host, self._port = host, port

    def app_url(self) -> str:
        """A fresh address with the session token: the "locked" page reloads itself with it."""
        return app_url(self._host, self._port)

    def toggle_fullscreen(self) -> bool:
        """WebView2 inside pywebview ignores the Fullscreen API, so the native window is toggled."""
        import webview

        if webview.windows:
            webview.windows[0].toggle_fullscreen()
            self._fullscreen = not self._fullscreen
        return self._fullscreen


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
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\VerbaScribe.Desktop.Window")
    if _instance_mutex and kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
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
    try:
        _pid_file().write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


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
    try:
        pid = int(_pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = 0
    MB_YESNO, MB_ICONWARNING, IDYES = 0x4, 0x30, 6
    answer = ctypes.windll.user32.MessageBoxW(
        None,
        "VerbaScribe уже запущен, но его окно не отвечает.\n\n"
        "Завершить зависший процесс и открыть программу заново? Незавершённая обработка будет прервана.",
        "VerbaScribe", MB_YESNO | MB_ICONWARNING | MB_ON_TOP)
    if answer != IDYES or not pid or pid == os.getpid():
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
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
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
        self._status = {"state": "idle", "text": "", "recording": False, "queue": []}
        self._running: dict = {}
        self._mf_default, self._mf_grayed = MF_DEFAULT, MF_GRAYED
        self.tray = Tray(ICO_PATH, self.menu_items, self.on_command, self.show_window)
        window.events.closing += self.on_closing

    def start(self) -> None:
        started = ICO_PATH.exists() and self.tray.start()
        print(f"[tray] icon {'shown' if started and self.tray.hwnd else 'not shown'}")
        if started:
            threading.Thread(target=self._poll, daemon=True, name="tray_status").start()

    # ---- status
    def _poll(self) -> None:
        from app.app_status import snapshot
        from app.database import get_meeting

        while not self.quitting:
            try:
                st = snapshot()
                self._status = st
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
            self.tray.notify(f"{label}: ошибка", title, warning=True)
        elif status == "cancelled":
            return
        else:
            self.tray.notify(f"{label} завершено", title)

    def _window_in_front(self) -> bool:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        return bool(hwnd) and user32.GetForegroundWindow() == hwnd and bool(user32.IsWindowVisible(hwnd))

    # ---- menu
    def menu_items(self):
        st = self._status
        return [
            ("Открыть VerbaScribe", "open", self._mf_default),
            (None, None, 0),
            (st.get("text") or "Готово к работе", None, self._mf_grayed),
            *self._record_items(st),
            ("Импорт файлов…", "import", 0),
            ("Вопрос по медиатеке…", "ask", 0),
            (None, None, 0),
            ("Настройки…", "settings", 0),
            ("Открыть папку данных", "data", 0),
            (None, None, 0),
            ("Выход", "quit", 0),
        ]

    def _record_items(self, st):
        """One entry per recording preset (app.js BUILTIN_PRESETS + the user's own)."""
        if st.get("recording"):
            return [("Остановить запись", "record", 0)]
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
        if cmd.startswith("record:"):
            self.show_window()
            import json

            preset = json.dumps(cmd.split(":", 1)[1])
            self.window.evaluate_js(f"window.trayCommand && window.trayCommand('record', {preset})")
            return
        if cmd == "open":
            self.show_window()
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
        if self.quitting or self._status.get("state") == "idle":
            self.quitting = True
            return True
        # Something runs: keep working in the tray instead of stopping it
        threading.Timer(0.05, self.window.hide).start()
        self.tray.notify("VerbaScribe работает в фоне",
                         "Запись или обработка продолжается. Значок в трее — открыть окно или выйти.")
        return False

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


def show_error_box(text: str) -> None:
    if sys.platform == "win32":
        try:
            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(None, text, "VerbaScribe", MB_ICONERROR | MB_ON_TOP)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def main() -> None:
    set_app_user_model_id()

    parser = argparse.ArgumentParser(description="VerbaScribe Desktop Studio")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--browser", action="store_true", help="Open in external web browser")
    parser.add_argument("--headless", action="store_true", help="Run server without GUI window")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        # Allow the explicitly chosen host in the server's Host header check
        os.environ["VERBASCRIBE_ALLOWED_HOSTS"] = args.host
    already_running = is_port_in_use(args.host, args.port)

    if args.headless:
        if not already_running:
            register_cuda_dlls()
            threading.Thread(
                target=lambda: wait_for_server(args.host, args.port)
                and print(f"VerbaScribe: {app_url(args.host, args.port)}", flush=True),
                daemon=True,
            ).start()
            uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=3)
        return

    if not args.browser:
        if not acquire_single_instance():
            # A second double-click shows the window that is already open instead of a second copy;
            # an instance that is just closing is waited for
            outcome = wait_for_other_instance()
            print(f"Another VerbaScribe instance: {outcome}")
            if outcome == "activated" or (outcome == "timeout" and not recover_stuck_instance()):
                return
        write_pid_file()
        # A previous instance may still be releasing the port for a moment
        for _ in range(50):
            if not is_port_in_use(args.host, args.port):
                break
            time.sleep(0.2)
        already_running = is_port_in_use(args.host, args.port)
        try:
            import webview

            if not already_running:
                print("Starting VerbaScribe local AI server...")
                start_server_in_thread(args.host, args.port)
                wait_for_server(args.host, args.port)

            url = app_url(args.host, args.port)
            webview.settings["ALLOW_DOWNLOADS"] = True
            window = webview.create_window(
                title=WINDOW_TITLE,
                url=url,
                width=1460,
                height=900,
                min_size=(1100, 700),
                background_color="#141416",
                text_select=True,
                js_api=WindowApi(args.host, args.port),
            )
            tray = None
            try:
                tray = TrayController(window)
                tray.start()
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
                    os._exit(0)

                threading.Thread(target=_hard_exit, daemon=True).start()

            window.events.closed += _exit_soon
            grant_media_to_app(window, args.host, args.port)
            icon_arg = str(ICO_PATH) if ICO_PATH.exists() else None
            # A persistent profile in the data folder: the page's own saved preferences (volume,
            # last tabs, ...) survive a restart; private mode (pywebview's default) forgets them
            from app.paths import DATA_DIR

            webview.start(func=apply_native_window_icon_and_hide_console, icon=icon_arg,
                          private_mode=False, storage_path=str(DATA_DIR / "webview"))
            if tray is not None:
                tray.stop()
            os._exit(0)
        except Exception as e:
            print(f"[!] Native window fallback to browser ({e})...")

    # Fallback: regular browser mode
    if already_running:
        webbrowser.open(app_url(args.host, args.port))
        return

    def _open_browser():
        wait_for_server(args.host, args.port)
        webbrowser.open(app_url(args.host, args.port))

    threading.Thread(target=_open_browser, daemon=True).start()
    register_cuda_dlls()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    from app.applog import LOG_FILE, start_file_log

    if "--headless" not in sys.argv:
        start_file_log()
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        import traceback

        traceback.print_exc()
        show_error_box(
            "Не удалось запустить VerbaScribe.\n\n"
            f"Подробности записаны в журнал:\n{LOG_FILE}\n\n"
            "VerbaScribe could not start. Details are in the log file above."
        )
        sys.exit(1)
