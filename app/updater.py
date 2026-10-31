"""
Update check and self-update from GitHub Releases.

Check: GET api.github.com/repos/<repo>/releases/latest (only that request; no data about the user
or the computer is sent). At most every CHECK_INTERVAL_SEC, and only if the setting
"update_check" is on.

Update (on the user's click):
  1. the release archive Verbaliox-vX.Y.Z.zip is downloaded into <data>/updates and checked
     against the release's SHA256SUMS.txt;
  2. this module is started as a separate process (`python -m app.updater apply ...`) and the app
     quits; the updater waits for it to exit, shows a small progress window, backs up the files it
     will replace, copies the new program files over the program folder (never the user's data,
     models or Python environments), installs Python packages again if requirements.txt changed,
     and starts Verbaliox.exe. On any failure the backup is restored.

Automatic updates (setting "update_auto", on by default): a newer release found by the check is
downloaded in the background; it is installed when the user clicks "Restart", or when the app is
closed (then it is not started again). The folder .git of a git checkout is never touched.
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
from typing import Any, Callable, Dict, List, Optional, Tuple

# Loaded before the update replaces the program files
from app.pyenv import ensure_pip, find_uv

GITHUB_REPO = "pravken/Verbaliox"
LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# With "Get beta versions" on: the newest of the recent releases, pre-releases (v0.5.0-beta.1) too;
# releases/latest never names a pre-release
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=30"
CHECK_INTERVAL_SEC = 6 * 3600
# Files are only ever downloaded from this repository's releases
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
# The author's release key (minisign format, app/release_sign.py, docs/release-signing.md). Set: an
# update installs only with SHA256SUMS.txt.minisig signed by it for that very release. Empty: the
# checksum alone is checked, as before the key existed.
RELEASE_PUBLIC_KEY = "RWT8fmjh8HjA/sAFcnc7vHWhzK+i+OircfoKXDPiKs5QnNnrGdiRRIpB"
# Never replaced by an update: user data, models and the Python environments
# .venv-new and .venv-old: the move to Python 3.11 (app/envmove.py)
PROTECTED = {".venv", ".venv-new", ".venv-old", ".venv-dub", ".python", "models", "data", "node_modules", ".git"}

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

    settings = get_all_settings()
    enabled = settings.get("update_check", "true").lower() == "true"
    beta = settings.get("update_beta", "false").lower() == "true"
    with _lock:
        # switching the beta channel asks again at once
        fresh = time.time() - _state["checked_at"] < CHECK_INTERVAL_SEC and _state.get("beta") == beta
    if (enabled or force) and (force or not fresh):
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True,
                              headers={"Accept": "application/vnd.github+json",
                                       "User-Agent": f"Verbaliox/{current_version()}"}) as c:
                r = c.get(RELEASES_URL if beta else LATEST_URL)
            rel = None
            if r.status_code != 404:  # 404: no release published yet
                r.raise_for_status()
                rel = r.json()
                if beta:
                    rel = max((x for x in rel if isinstance(x, dict) and not x.get("draft")),
                              key=lambda x: parse_version(x.get("tag_name") or ""), default=None)
            if rel is None:
                latest = None
            else:
                assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []
                          if str(a.get("browser_download_url") or "").startswith(DOWNLOAD_PREFIX)}
                # GitHub's own SHA-256 of each uploaded file ("sha256:<hex>"), independent of SHA256SUMS.txt
                digests = {a.get("name"): str(a.get("digest") or "") for a in rel.get("assets") or []}
                zip_name = next((n for n in assets if re.fullmatch(r"Verbaliox-v[\w.\-]+\.zip", n or "")), None)
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
                    "sig_url": assets.get("SHA256SUMS.txt.minisig"),
                    "zip_digest": digests.get(zip_name, "").removeprefix("sha256:").lower() if zip_name else "",
                }
            with _lock:
                _state.update(checked_at=time.time(), latest=latest, error="", beta=beta)
        except Exception as e:
            with _lock:
                _state.update(checked_at=time.time(), error=str(e)[:200], beta=beta)
    st = status()
    _auto_download(st)
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
    out["auto"] = auto_enabled()
    # A git clone is updated with git: an archive would overwrite the working copy (and its changes)
    out["can_install"] = out["available"] and bool(latest and latest.get("zip_url")) and not out["git_checkout"]
    return out


def auto_enabled() -> bool:
    from app.database import get_all_settings

    return get_all_settings().get("update_auto", "true").lower() == "true"


def _auto_download(st: Dict[str, Any]) -> None:
    """Starts downloading a found update when automatic updates are on."""
    dl = st.get("download") or {}
    if st.get("can_install") and st.get("auto") and not dl.get("active") and not dl.get("done"):
        try:
            start_download()
        except RuntimeError:
            pass


def ready_to_install() -> bool:
    """A downloaded update that automatic updates would install now."""
    with _lock:
        done = bool(_state["download"].get("done") and _state["download"].get("path"))
    return done and auto_enabled() and not is_git_checkout()


def install_on_exit() -> bool:
    """Called when the app closes: installs a downloaded update without starting the app again."""
    try:
        with _lock:
            path = _state["download"].get("path") if _state["download"].get("done") else ""
        if not path or not auto_enabled() or is_git_checkout():
            return False
        launch_installer(restart=False)
        return True
    except Exception:
        traceback.print_exc()
        return False


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


def check_signature(sums: bytes, sig_text: str, tag: str, key: Optional[str] = None) -> None:
    """RuntimeError unless `sig_text` is the release key's signature of `sums` for release `tag`."""
    from app import release_sign

    if not sig_text.strip():
        raise RuntimeError("Релиз не подписан ключом автора — обновление отменено")
    try:
        comment = release_sign.verify_file(key or RELEASE_PUBLIC_KEY, sums, sig_text)
    except release_sign.SignatureError as e:
        print(f"[update] signature: {e}", flush=True)
        raise RuntimeError("Подпись релиза не прошла проверку — обновление отменено")
    if f"tag:{tag}" not in comment.split():  # a signature of another release, put next to this one
        print(f"[update] signature is for another release: {comment}", flush=True)
        raise RuntimeError("Подпись релиза не прошла проверку — обновление отменено")


def _download(latest: Dict[str, Any]) -> None:
    import httpx

    def _set(**kw) -> None:
        with _lock:
            _state["download"].update(kw)

    try:
        target = _updates_dir() / latest["zip_name"]
        part = target.with_suffix(".part")
        with httpx.Client(timeout=60.0, follow_redirects=True,
                          headers={"User-Agent": f"Verbaliox/{current_version()}"}) as c:
            expected = None
            if latest.get("sums_url"):
                sums = c.get(latest["sums_url"]).content
                if RELEASE_PUBLIC_KEY:
                    check_signature(sums, c.get(latest["sig_url"]).text if latest.get("sig_url") else "", latest.get("tag") or "")
                for line in sums.decode("utf-8", "replace").splitlines():
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


def launch_installer(restart: bool = True, minimized: bool = False) -> None:
    """Starts the updater for the downloaded archive; the caller then quits the app. minimized: the
    app comes back in the tray, without its window (installed in the background, run.py)."""
    from app.paths import APP_DIR, DATA_DIR

    with _lock:
        path = _state["download"].get("path")
    if not path or not Path(path).is_file():
        raise RuntimeError("Архив обновления не скачан")
    cmd = [sys.executable, "-m", "app.updater", "apply", "--zip", path, "--app-dir", str(APP_DIR),
           "--data-dir", str(DATA_DIR), "--pid", str(os.getpid())] + ([] if restart else ["--no-restart"]) \
        + (["--restart-minimized"] if restart and minimized else [])
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, cwd=str(APP_DIR), creationflags=flags, close_fds=True)


# ------------------------------------------------------------------ apply (separate process)
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _dub_requirements_hash(app_dir: Path) -> str:
    """The same hash app/dub_setup.py records in its ready mark: both dubbing locks together."""
    files = [app_dir / "requirements-dub.txt", app_dir / "requirements-dub-voxcpm.txt"]
    return hashlib.sha256(b"".join(f.read_bytes() for f in files if f.is_file())).hexdigest()


class UpdateFailed(RuntimeError):
    """An update that did not install. `outcome` says what the program folder holds now:
    "untouched" (nothing was replaced yet), "restored" (the previous version is back) or "broken"
    (putting it back failed too)."""

    def __init__(self, message: str, outcome: str = "untouched") -> None:
        super().__init__(message)
        self.outcome = outcome


OUTCOME_TEXT = {
    "untouched": "Файлы программы не изменены.",
    "restored": "Прежняя версия восстановлена.",
    "broken": "Прежнюю версию восстановить не удалось: скачайте и распакуйте Verbaliox заново "
              "(ваши записи и настройки в папке data не пострадают; подробности в logs\\update.log).",
}

# Program code an update owns: a file of these kinds that the new version no longer has is removed
# (an old module left in app/ could still be imported)
CODE_DIRS = ("app", "static")
CODE_SUFFIXES = {".py", ".js", ".html", ".css"}
# The dubbing environment's ready mark (app/dub_setup.py DUB_READY_MARKER)
DUB_MARKER = Path(".venv-dub") / "verbaliox-dub-ready.txt"
FORMER_DUB_MARKER = Path(".venv-dub") / "verbascribe-dub-ready.txt"  # made while the program was VerbaScribe


def _wait_for_exit(pid: int, timeout: float = 120.0) -> bool:
    """False if the process is still running after `timeout` (it may be finishing a job)."""
    if sys.platform != "win32":
        return True
    import ctypes

    SYNCHRONIZE, WAIT_OBJECT_0 = 0x00100000, 0
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if h:
        ended = ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout * 1000)) == WAIT_OBJECT_0
        ctypes.windll.kernel32.CloseHandle(h)
        if not ended:
            return False
    time.sleep(1.0)  # files and the port are released a moment later
    return True


def _stale_code_files(app_dir: Path, new_files: List[str]) -> List[str]:
    """Code files of the installed version that the new one does not ship."""
    new = set(new_files)
    if not any(rel.startswith("app/") for rel in new):  # not a full program archive: remove nothing
        return []
    out = []
    for top in CODE_DIRS:
        base = app_dir / top
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in CODE_SUFFIXES and p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(app_dir).as_posix()
                if rel not in new:
                    out.append(rel)
    return sorted(out)


def _preflight(app_dir: Path, need_bytes: int) -> None:
    """Fails before anything is replaced when the program folder is not writable or the disk is full."""
    probe = app_dir / ".update-write-test"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        raise UpdateFailed(f"Нет права записи в папку программы {app_dir} ({e}). "
                           "Переместите Verbaliox в папку пользователя или запустите от имени администратора.")
    try:
        free = shutil.disk_usage(app_dir).free
    except OSError:
        return
    if free < need_bytes:
        raise UpdateFailed(f"Недостаточно места на диске: нужно около {need_bytes / 1e9:.1f} ГБ, "
                           f"свободно {free / 1e9:.1f} ГБ.")


def _mark_dub_outdated(app_dir: Path, old_hash: str) -> None:
    """requirements-dub.txt changed: the dubbing environment needs its packages updated. A ready
    mark that records the requirements it was made with notices that itself; an older mark gets
    the previous requirements written in, so it reads as outdated."""
    marker = app_dir / DUB_MARKER
    if not marker.is_file() and (app_dir / FORMER_DUB_MARKER).is_file():
        marker = app_dir / FORMER_DUB_MARKER  # app/dub_setup.py renames it at the next start
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError:
        return
    if not re.search(r"^requirements=", text, re.M):
        try:
            marker.write_text(text.rstrip("\n") + f"\nrequirements={old_hash}\n", encoding="utf-8")
        except OSError:  # the update itself is fine; the next dub check shows what is missing
            pass


def _freeze(py: Path, uv: Optional[str]) -> str:
    if not uv:
        ensure_pip(str(py))
    cmd = [uv, "pip", "freeze", "--python", str(py)] if uv else [str(py), "-m", "pip", "freeze"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW, timeout=120)
        return res.stdout if res.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# The PyTorch wheels of this version (requirements.txt names its index too) and of the versions before
# it: putting back the packages of an older version needs that one's (torch 2.6 was cu124)
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
OLDER_TORCH_INDEXES = ["https://download.pytorch.org/whl/cu124"]


def _reinstall(py: Path, uv: Optional[str], frozen_file: Path, log_dir: Path) -> None:
    indexes = [a for url in [TORCH_INDEX, *OLDER_TORCH_INDEXES] for a in ("--extra-index-url", url)]
    cmd = ([uv, "pip", "install", "--python", str(py), "-r", str(frozen_file),
            "--index-strategy", "unsafe-best-match", *indexes]
           if uv else [str(py), "-m", "pip", "install", "-r", str(frozen_file), *indexes])
    with open(log_dir / "update.log", "a", encoding="utf-8") as lf:
        subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)


def archive_files(names: List[str], app_dir: Path) -> List[Tuple[str, str]]:
    """(name in the archive, path in the program folder) of the files an update writes. The archive
    holds one top folder (Verbaliox-vX.Y.Z/); a name that would land outside the program folder
    (absolute, a drive, "..", backslashes from another zip tool) or in a protected folder is left out."""
    root = app_dir.resolve()
    norm = [(n, n.replace("\\", "/")) for n in names if not n.replace("\\", "/").endswith("/")]
    first = {x.split("/")[0] for _n, x in norm}
    prefix = first.pop() + "/" if len(first) == 1 and all("/" in x for _n, x in norm) else ""
    out = []
    for n, x in norm:
        rel = x[len(prefix):] if prefix and x.startswith(prefix) else x
        parts = rel.split("/")
        if (not rel or rel.startswith("/") or ":" in rel or any(p in ("", ".", "..") for p in parts)
                or parts[0].lower() in PROTECTED):
            continue
        try:
            (root / rel).resolve().relative_to(root)
        except ValueError:
            continue
        out.append((n, rel))
    return out


def apply_update(zip_path: Path, app_dir: Path, data_dir: Path, report: Callable[[str], None]) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    backup = data_dir / "updates" / "backup.zip"
    old_req = _sha256(app_dir / "requirements.txt")
    old_dub_req = _dub_requirements_hash(app_dir)
    replaced: List[str] = []
    restore_packages: Optional[Callable[[], None]] = None

    with zipfile.ZipFile(zip_path) as z:
        files = archive_files(z.namelist(), app_dir)
        stale = _stale_code_files(app_dir, [rel for _n, rel in files])
        new_req = next((hashlib.sha256(z.read(n)).hexdigest() for n, rel in files if rel == "requirements.txt"), old_req)
        # The new files, their backup in the data folder and, with new libraries, room to install them
        size = sum(z.getinfo(n).file_size for n, _rel in files)
        _preflight(app_dir, 2 * size + 200_000_000 + (3_000_000_000 if new_req != old_req else 0))
        report(f"Резервная копия ({len(files)} файлов)…")
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as bz:
            for rel in [rel for _n, rel in files] + stale:
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
            for rel in stale:
                (app_dir / rel).unlink(missing_ok=True)
            if _sha256(app_dir / "requirements.txt") != old_req:
                report("Установка библиотек Python (может занять несколько минут)…")
                py = app_dir / ".venv" / "Scripts" / "python.exe"
                uv = find_uv()
                # What is installed now: put back if the new libraries fail half way
                frozen = _freeze(py, uv)
                if frozen:
                    frozen_file = data_dir / "updates" / "packages-before-update.txt"
                    frozen_file.write_text(frozen, encoding="utf-8")
                    restore_packages = (lambda: _reinstall(py, uv, frozen_file, log_dir))
                cmd = ([uv, "pip", "install", "--python", str(py), "-r", str(app_dir / "requirements.txt"),
                        "--require-hashes", "--index-strategy", "unsafe-best-match", "--extra-index-url", TORCH_INDEX]
                       if uv else
                       [str(py), "-m", "pip", "install", "-r", str(app_dir / "requirements.txt"),
                        "--require-hashes", "--extra-index-url", TORCH_INDEX])
                with open(log_dir / "update.log", "a", encoding="utf-8") as lf:
                    res = subprocess.run(cmd, cwd=str(app_dir), stdout=lf, stderr=subprocess.STDOUT,
                                         creationflags=_NO_WINDOW)
                if res.returncode != 0:
                    raise RuntimeError("Не удалось установить библиотеки (подробности в logs\\update.log)")
            if _dub_requirements_hash(app_dir) != old_dub_req:
                _mark_dub_outdated(app_dir, old_dub_req)
        except Exception as e:
            report("Ошибка — восстановление прежней версии…")
            outcome = "restored"
            # Each step on its own: a failure here must not hide the original error
            try:
                with zipfile.ZipFile(backup) as bz:
                    bz.extractall(app_dir)
                kept = set(replaced)
                for _n, rel in files:  # files the new version added
                    if rel not in kept:
                        (app_dir / rel).unlink(missing_ok=True)
            except Exception:
                outcome = "broken"
                with open(log_dir / "update.log", "a", encoding="utf-8") as lf:
                    lf.write("restore of the program files failed:\n" + traceback.format_exc())
            if restore_packages:
                report("Восстановление прежних библиотек Python…")
                try:
                    restore_packages()
                except Exception:
                    outcome = "broken"
                    with open(log_dir / "update.log", "a", encoding="utf-8") as lf:
                        lf.write("restore of the Python packages failed:\n" + traceback.format_exc())
            raise UpdateFailed(str(e), outcome) from e


def _main(argv: List[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("action")
    ap.add_argument("--zip", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--no-restart", action="store_true")  # installed on exit: the user closed the app
    ap.add_argument("--restart-minimized", action="store_true")  # installed in the background: back in the tray
    a = ap.parse_args(argv)
    app_dir, data_dir = Path(a.app_dir), Path(a.data_dir)

    ui = _ProgressWindow(visible=not a.restart_minimized)  # in the background: no window, only a failure is said
    ok, message = True, ""

    def work() -> None:
        nonlocal ok, message
        try:
            ui.set("Ожидание закрытия Verbaliox…")
            if a.pid and not _wait_for_exit(a.pid):
                # Replacing files under a running program would mix two versions
                raise UpdateFailed("Verbaliox не закрылся за две минуты. Закройте программу и установите обновление снова.")
            apply_update(Path(a.zip), app_dir, data_dir, ui.set)
            ui.set("Готово." if a.no_restart else "Готово. Запуск Verbaliox…")
        except Exception as e:
            ok, message = False, f"{e}\n{OUTCOME_TEXT[getattr(e, 'outcome', 'untouched')]}"
            with open(data_dir / "logs" / "update.log", "a", encoding="utf-8") as lf:
                lf.write(traceback.format_exc())
        finally:
            launcher = app_dir / "Verbaliox.exe"
            pythonw = app_dir / ".venv" / "Scripts" / "pythonw.exe"
            if a.restart_minimized and pythonw.is_file():  # after a failure too: the previous version is back
                subprocess.Popen([str(pythonw), str(app_dir / "run.py"), "--minimized"], cwd=str(app_dir))
            elif launcher.is_file() and not (a.no_restart and ok):
                subprocess.Popen([str(launcher)], cwd=str(app_dir))
            ui.close(None if ok else f"Обновление не установлено: {message}")

    threading.Thread(target=work, daemon=True).start()
    ui.run()
    return 0 if ok else 1


class _ProgressWindow:
    """A small always-on-top window; falls back to nothing if tkinter is unavailable. visible=False
    (installed in the background): no window, a failure is told in a message box."""

    def __init__(self, visible: bool = True) -> None:
        self._tk = None
        self._msg = "Обновление Verbaliox…"
        self._visible = visible
        if not visible:
            return
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title("Обновление Verbaliox")
            root.geometry("420x110")
            root.configure(bg="#1c1c1e")
            root.attributes("-topmost", True)
            root.resizable(False, False)
            tk.Label(root, text="Обновление Verbaliox", fg="white", bg="#1c1c1e",
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
            if error and not self._visible and sys.platform == "win32":
                import ctypes

                ctypes.windll.user32.MessageBoxW(None, error, "Verbaliox", 0x10 | 0x40000)  # error, on top
            return

        def _close() -> None:
            if error:
                from tkinter import messagebox

                messagebox.showerror("Verbaliox", error, parent=self._tk)
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
