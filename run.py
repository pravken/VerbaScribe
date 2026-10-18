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
DATA_DIR = BASE_DIR / "data"

import uvicorn
from app.hardware import register_cuda_dlls


def hide_console_window() -> None:
    """
    Hides the console window once the native desktop GUI window is open.
    """
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
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


def start_server_in_thread(host: str, port: int) -> threading.Thread:
    register_cuda_dlls()
    config = uvicorn.Config("app.main:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="uvicorn_server")
    t.start()
    return t


def main() -> None:
    parser = argparse.ArgumentParser(description="Whisper Meeting Studio")
    parser.add_argument("--host", default="127.0.0.1", help="Хост сервера")
    parser.add_argument("--port", type=int, default=8765, help="Порт сервера")
    parser.add_argument("--browser", action="store_true", help="Открыть в обычном браузере вместо нативного окна")
    parser.add_argument("--headless", action="store_true", help="Только сервер без открытия окна")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    already_running = is_port_in_use(args.host, args.port)

    if args.headless:
        if not already_running:
            register_cuda_dlls()
            uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
        return

    if not args.browser:
        try:
            import webview

            if not already_running:
                print("Запуск локального ИИ-сервера VerbaScribe...")
                start_server_in_thread(args.host, args.port)
                wait_for_server(args.host, args.port)

            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.create_window(
                title="VerbaScribe — ИИ-транскрибация и протоколы встреч",
                url=url,
                width=1460,
                height=900,
                min_size=(1080, 680),
                background_color="#141416",
                text_select=True,
            )
            webview.start(func=hide_console_window)
            os._exit(0)
        except Exception as e:
            print(f"[!] Не удалось открыть нативное окно pywebview ({e}), переключаюсь на браузер...")

    # Fallback: regular browser mode
    if already_running:
        webbrowser.open(url)
        return

    def _open_browser():
        wait_for_server(args.host, args.port)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()
    register_cuda_dlls()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
