"""
Update check and self-update from GitHub Releases.

Check: GET api.github.com/repos/<repo>/releases/latest (only that request; no data about the user
or the computer is sent). At most every CHECK_INTERVAL_SEC, and only if the setting
"update_check" is on.

Update (on the user's click):
  1. the release archive VerbaScribe-vX.Y.Z.zip is downloaded into <data>/updates and checked
     against the release's SHA256SUMS.txt;
  2. this module is started as a separate process (`python -m app.updater apply ...`) and the app
     quits; the updater waits for it to exit, shows a small progress window, backs up the files it
     will replace, copies the new program files over the program folder (never the user's data,
     models or Python environments), installs Python packages again if requirements.txt changed,
     and starts VerbaScribe.exe. On any failure the backup is restored.

A program folder that is a git checkout is never overwritten: it is updated with git pull.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

GITHUB_REPO = "pravken/VerbaScribe"
LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CHECK_INTERVAL_SEC = 6 * 3600
# Files are only ever downloaded from this repository's releases
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
# Never replaced by an update: user data, models and the Python environments
PROTECTED = {".venv", ".venv-dub", ".python", "models", "data", "node_modules", ".git"}

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

_state: Dict[str, Any] = {
    "checked_at": 0.0, "latest": None, "error": "",
    "download": {"active": False, "progress": 0, "done": False, "error": "", "path": ""},
}
_lock = threading.Lock()


# ------------------------------------------------------------------ versions
_PRE_RANK = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}


def parse_version(text: str) -> tuple:
    """'v1.10.2' -> (1, 10, 2, 1, 0, 0); '0.2.0-beta.1' -> (0, 2, 0, 0, 1, 1): a pre-release sorts
    below its release (alpha < beta < rc), so a beta tester is offered the final version.
    Anything unparsable sorts lowest."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)(?:[-.]?(alpha|beta|rc|a|b)\.?(\d*))?", str(text or ""), re.IGNORECASE)
    if not m:
        return (0, 0, 0, 0, 0, 0)
    nums = [int(n) for n in m.group(1).split(".")[:3]]
    nums += [0] * (3 - len(nums))
    if m.group(2):
        return (*nums, 0, _PRE_RANK[m.group(2).lower()], int(m.group(3) or 0))
    return (*nums, 1, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def current_version() -> str:
    from app import __version__

    return __version__


def is_git_checkout() -> bool:
    from app.paths import APP_DIR

    return (APP_DIR / ".git").exists()


# ------------------------------------------------------------------ check
def check(force: bool = False) -> Dict[str, Any]:
    """The latest release, cached; never raises."""
    import httpx

    from app.database import get_all_settings

    enabled = get_all_settings().get("update_check", "true").lower() == "true"
    with _lock:
        fresh = time.time() - _state["checked_at"] < CHECK_INTERVAL_SEC
    if (enabled or force) and (force or not fresh):
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True,
                              headers={"Accept": "application/vnd.github+json",
                                       "User-Agent": f"VerbaScribe/{current_version()}"}) as c:
                r = c.get(LATEST_URL)
            if r.status_code == 404:  # no release published yet
                latest = None
            else:
                r.raise_for_status()
                rel = r.json()
                assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []
                          if str(a.get("browser_download_url") or "").startswith(DOWNLOAD_PREFIX)}
                # GitHub's own SHA-256 of each uploaded file ("sha256:<hex>"), independent of SHA256SUMS.txt
                digests = {a.get("name"): str(a.get("digest") or "") for a in rel.get("assets") or []}
                zip_name = next((n for n in assets if re.fullmatch(r"VerbaScribe-v[\w.\-]+\.zip", n or "")), None)
                latest = {
                    "version": str(rel.get("tag_name") or "").lstrip("v"),
                    "tag": rel.get("tag_name"),
                    "name": rel.get("name") or rel.get("tag_name"),
                    "notes": (rel.get("body") or "")[:20000],
                    "url": rel.get("html_url"),
                    "published_at": rel.get("published_at"),
                    "zip_url": assets.get(zip_name) if zip_name else None,
                    "zip_name": zip_name,
                    "sums_url": assets.get("SHA256SUMS.txt"),
                    "zip_digest": digests.get(zip_name, "").removeprefix("sha256:").lower() if zip_name else "",
                }
            with _lock:
                _state.update(checked_at=time.time(), latest=latest, error="")
        except Exception as e:
            with _lock:
                _state.update(checked_at=time.time(), error=str(e)[:200])
    return status()


def status() -> Dict[str, Any]:
    from app.database import get_all_settings

    with _lock:
        latest = _state["latest"]
        out = {
            "current": current_version(),
            "latest": latest,
            "available": bool(latest and is_newer(latest["version"], current_version())),
            "checked_at": _state["checked_at"],
            "error": _state["error"],
            "enabled": get_all_settings().get("update_check", "true").lower() == "true",
            "git_checkout": is_git_checkout(),
            "download": dict(_state["download"]),
        }
    out["can_install"] = out["available"] and not out["git_checkout"] and bool(latest and latest.get("zip_url"))
    return out


# ------------------------------------------------------------------ download
def _updates_dir() -> Path:
    from app.paths import DATA_DIR

    d = DATA_DIR / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_download() -> Dict[str, Any]:
    st = status()
    if not st["can_install"]:
        raise RuntimeError("Обновление недоступно для установки")
    with _lock:
        if _state["download"]["active"]:
            return status()
        _state["download"] = {"active": True, "progress": 0, "done": False, "error": "", "path": ""}
    threading.Thread(target=_download, args=(st["latest"],), daemon=True, name="update_download").start()
    return status()


def _download(latest: Dict[str, Any]) -> None:
    import httpx

    def _set(**kw) -> None:
        with _lock:
            _state["download"].update(kw)

    try:
        target = _updates_dir() / latest["zip_name"]
        part = target.with_suffix(".part")
        with httpx.Client(timeout=60.0, follow_redirects=True,
                          headers={"User-Agent": f"VerbaScribe/{current_version()}"}) as c:
            expected = None
            if latest.get("sums_url"):
                for line in c.get(latest["sums_url"]).text.splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1].lstrip("*") == latest["zip_name"]:
                        expected = parts[0].lower()
            if not expected:
                raise RuntimeError("В релизе нет контрольной суммы архива (SHA256SUMS.txt)")
            if latest.get("zip_digest") and latest["zip_digest"] != expected:
                raise RuntimeError("Контрольные суммы релиза не совпадают между собой — обновление отменено")
            h = hashlib.sha256()
            with c.stream("GET", latest["zip_url"]) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                done = 0
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if total:
                            _set(progress=int(100 * done / total))
        if h.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError("Контрольная сумма архива не совпала — файл повреждён или подменён")
        part.replace(target)
        _set(active=False, done=True, progress=100, path=str(target))
    except Exception as e:
        traceback.print_exc()
        _set(active=False, done=False, error=str(e)[:300])


# Called before the process ends for an update (run.py registers removing the tray icon)
exit_hooks: List[Callable[[], None]] = []


def quit_soon(delay: float = 0.8) -> None:
    """Ends the app shortly after the HTTP answer has been sent, so the updater can replace files."""

    def _quit() -> None:
        time.sleep(delay)
        for hook in list(exit_hooks):
            try:
                hook()
            except Exception:
                pass
        os._exit(0)

    threading.Thread(target=_quit, daemon=True, name="update_quit").start()


def launch_installer() -> None:
    """Starts the updater for the downloaded archive; the caller then quits the app."""
    from app.paths import APP_DIR, DATA_DIR

    with _lock:
        path = _state["download"].get("path")
    if not path or not Path(path).is_file():
        raise RuntimeError("Архив обновления не скачан")
    cmd = [sys.executable, "-m", "app.updater", "apply", "--zip", path, "--app-dir", str(APP_DIR),
           "--data-dir", str(DATA_DIR), "--pid", str(os.getpid())]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, cwd=str(APP_DIR), creationflags=flags, close_fds=True)


# ------------------------------------------------------------------ apply (separate process)
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _wait_for_exit(pid: int, timeout: float = 60.0) -> None:
    if sys.platform != "win32":
        return
    import ctypes

    SYNCHRONIZE = 0x00100000
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if h:
        ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout * 1000))
        ctypes.windll.kernel32.CloseHandle(h)
    time.sleep(1.0)  # files and the port are released a moment later


def _find_uv() -> Optional[str]:
    found = shutil.which("uv")
    if found:
        return found
    for p in (Path.home() / ".local" / "bin" / "uv.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "uv.exe"):
        if p.is_file():
            return str(p)
    return None


def apply_update(zip_path: Path, app_dir: Path, data_dir: Path, report: Callable[[str], None]) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    backup = data_dir / "updates" / "backup.zip"
    old_req = _sha256(app_dir / "requirements.txt")
    replaced: List[str] = []

    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        prefix = os.path.commonprefix(names).split("/")[0] + "/" if names and "/" in names[0] else ""
        files = []
        for n in names:
            rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
            if not rel or rel.split("/")[0] in PROTECTED or ".." in Path(rel).parts:
                continue
            files.append((n, rel))
        report(f"Резервная копия ({len(files)} файлов)…")
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as bz:
            for _n, rel in files:
                p = app_dir / rel
                if p.is_file():
                    bz.write(p, rel)
                    replaced.append(rel)
        try:
            report("Замена файлов программы…")
            for i, (n, rel) in enumerate(files):
                dest = app_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                for attempt in range(10):  # the launcher may still be closing its splash window
                    try:
                        with z.open(n) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out)
                        break
                    except PermissionError:
                        if attempt == 9:
                            raise
                        time.sleep(1.0)
                if i % 50 == 0:
                    report(f"Замена файлов программы… {i}/{len(files)}")
            if _sha256(app_dir / "requirements.txt") != old_req:
                report("Установка библиотек Python (может занять несколько минут)…")
                py = app_dir / ".venv" / "Scripts" / "python.exe"
                uv = _find_uv()
                torch_index = "https://download.pytorch.org/whl/cu124"
                cmd = ([uv, "pip", "install", "--python", str(py), "-r", str(app_dir / "requirements.txt"),
                        "--require-hashes", "--index-strategy", "unsafe-best-match", "--extra-index-url", torch_index]
                       if uv else
                       [str(py), "-m", "pip", "install", "-r", str(app_dir / "requirements.txt"),
                        "--require-hashes", "--extra-index-url", torch_index])
                with open(log_dir / "update.log", "a", encoding="utf-8") as lf:
                    res = subprocess.run(cmd, cwd=str(app_dir), stdout=lf, stderr=subprocess.STDOUT,
                                         creationflags=_NO_WINDOW)
                if res.returncode != 0:
                    raise RuntimeError("Не удалось установить библиотеки (подробности в logs\\update.log)")
        except Exception:
            report("Ошибка — восстановление прежней версии…")
            with zipfile.ZipFile(backup) as bz:
                bz.extractall(app_dir)
            kept = set(replaced)
            for _n, rel in files:  # files the new version added
                if rel not in kept:
                    (app_dir / rel).unlink(missing_ok=True)
            raise


def _main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("action")
    ap.add_argument("--zip", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--pid", type=int, default=0)
    a = ap.parse_args(argv)
    app_dir, data_dir = Path(a.app_dir), Path(a.data_dir)

    ui = _ProgressWindow()
    ok, message = True, ""

    def work() -> None:
        nonlocal ok, message
        try:
            ui.set("Ожидание закрытия VerbaScribe…")
            if a.pid:
                _wait_for_exit(a.pid)
            apply_update(Path(a.zip), app_dir, data_dir, ui.set)
            ui.set("Готово. Запуск VerbaScribe…")
        except Exception as e:
            ok, message = False, str(e)
            with open(data_dir / "logs" / "update.log", "a", encoding="utf-8") as lf:
                lf.write(traceback.format_exc())
        finally:
            launcher = app_dir / "VerbaScribe.exe"
            if launcher.is_file():
                subprocess.Popen([str(launcher)], cwd=str(app_dir))
            ui.close(None if ok else f"Обновление не установлено: {message}\nПрежняя версия восстановлена.")

    threading.Thread(target=work, daemon=True).start()
    ui.run()
    return 0 if ok else 1


class _ProgressWindow:
    """A small always-on-top window; falls back to nothing if tkinter is unavailable."""

    def __init__(self) -> None:
        self._tk = None
        self._msg = "Обновление VerbaScribe…"
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title("Обновление VerbaScribe")
            root.geometry("420x110")
            root.configure(bg="#1c1c1e")
            root.attributes("-topmost", True)
            root.resizable(False, False)
            tk.Label(root, text="Обновление VerbaScribe", fg="white", bg="#1c1c1e",
                     font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=20, pady=(18, 4))
            self._var = tk.StringVar(value=self._msg)
            tk.Label(root, textvariable=self._var, fg="#98989d", bg="#1c1c1e",
                     font=("Segoe UI", 9), wraplength=380, justify="left").pack(anchor="w", padx=20)
            self._tk = root
        except Exception:
            self._tk = None

    def set(self, text: str) -> None:
        self._msg = text
        if self._tk is not None:
            self._tk.after(0, lambda: self._var.set(text))

    def close(self, error: Optional[str]) -> None:
        if self._tk is None:
            return

        def _close() -> None:
            if error:
                from tkinter import messagebox

                messagebox.showerror("VerbaScribe", error, parent=self._tk)
            self._tk.destroy()

        self._tk.after(0, _close)

    def run(self) -> None:
        if self._tk is not None:
            self._tk.mainloop()
        else:
            while threading.active_count() > 1:
                time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
