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


def wait_for_server(host: str, port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_port_in_use(host, port):
            return True
        time.sleep(0.1)
    return False


def read_session_token(timeout: float = 5.0) -> str:
    """Reads the per-launch session token written by the running server (app.main)."""
    from app.paths import SESSION_TOKEN_FILE as token_file

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
    register_cuda_dlls()
    config = uvicorn.Config("app.main:app", host=host, port=port, log_level="warning", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="uvicorn_server")
    t.start()
    return t


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
        try:
            import webview

            if not already_running:
                print("Starting VerbaScribe local AI server...")
                start_server_in_thread(args.host, args.port)
                wait_for_server(args.host, args.port)

            url = app_url(args.host, args.port)
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.create_window(
                title=WINDOW_TITLE,
                url=url,
                width=1460,
                height=900,
                min_size=(1100, 700),
                background_color="#141416",
                text_select=True,
            )
            icon_arg = str(ICO_PATH) if ICO_PATH.exists() else None
            webview.start(func=apply_native_window_icon_and_hide_console, icon=icon_arg)
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
    main()
