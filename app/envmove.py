"""
The move of the program's Python environment (.venv) to Python 3.11, once, for installations made
on Python 3.10.

An update is installed by the version being replaced (app/updater.py runs in its environment), so
it cannot make a new environment itself: it puts the new requirements.txt into the old .venv (the
lock installs on 3.10 as well, tools/lock_for_python310.py), and this version, started on 3.10,
moves itself:

1. `launch()` at the start (run.py), while the window is not open yet: a new environment
   .venv-new on Python 3.11 is created next to .venv (the signed python.org Python with Smart App
   Control on, as setup.bat does, else a uv-managed one), and `run` is started in Python 3.11
   itself, outside both environments; the app quits.
2. `run` waits for the app to end, installs requirements.txt into .venv-new and swaps the folders:
   .venv -> .venv-old, .venv-new -> .venv. Any failure leaves .venv as it was (.venv-new is removed)
   and says so; the next try is a day later. Then VerbaScribe starts again.
3. The next start on Python 3.11 removes .venv-old (`cleanup()`).

update.bat (a folder cloned with git) does steps 1-2 itself with `prepare` and `run --no-restart`.
Everything here is the standard library: `run` works in a Python without the app's packages.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

NEEDED = (3, 11)
NEW_ENV, OLD_ENV = ".venv-new", ".venv-old"
FAILED_MARK = "envmove-failed.txt"  # in <data>/updates: when the last try failed
RETRY_AFTER = 24 * 3600
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(data_dir: Path, line: str) -> None:
    try:
        (data_dir / "logs").mkdir(parents=True, exist_ok=True)
        with open(data_dir / "logs" / "update.log", "a", encoding="utf-8") as f:
            f.write(f"[envmove {time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


def needed(app_dir: Path, data_dir: Path) -> bool:
    """This app runs in the program's own .venv on an older Python, and no try failed lately."""
    if sys.platform != "win32" or sys.version_info[:2] >= NEEDED or os.environ.get("VERBASCRIBE_NO_ENV_MOVE"):
        return False
    try:
        if Path(sys.prefix).resolve() != (app_dir / ".venv").resolve():
            return False  # a developer's own environment: not ours to replace
    except OSError:
        return False
    try:
        return time.time() - (data_dir / "updates" / FAILED_MARK).stat().st_mtime > RETRY_AFTER
    except OSError:
        return True


def smart_app_control_on() -> bool:
    """Smart App Control blocks unsigned program files: the Python that uv downloads is unsigned."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            return int(winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")[0]) in (1, 2)
    except (OSError, ValueError, ImportError):
        return False


def signed_python() -> Optional[str]:
    """Python 3.11 from python.org (setup.bat :find_signed_python), never the Microsoft Store one."""
    for base, sub in ((os.environ.get("LOCALAPPDATA"), r"Programs\Python\Python311"),
                      (os.environ.get("ProgramFiles"), "Python311")):
        if base and (Path(base) / sub / "python.exe").is_file():
            return str(Path(base) / sub / "python.exe")
    try:
        out = subprocess.run(["py", "-3.11", "-c", "import sys; print(sys.executable)"], capture_output=True,
                             text=True, timeout=30, creationflags=_NO_WINDOW).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out if out and "windowsapps" not in out.lower() and Path(out).is_file() else None


def _base_python(env: Path) -> Optional[str]:
    """The Python an environment was made from (pyvenv.cfg "home")."""
    try:
        for line in (env / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home":
                p = Path(value.strip()) / "python.exe"
                return str(p) if p.is_file() else None
    except OSError:
        pass
    return None


def prepare(app_dir: Path, data_dir: Path) -> str:
    """Creates .venv-new on Python 3.11 and returns the Python it was made from."""
    from app.pyenv import find_uv

    new = app_dir / NEW_ENV
    shutil.rmtree(new, ignore_errors=True)
    uv = find_uv()
    py = None
    if smart_app_control_on():
        py = signed_python()
        if not py:
            _log(data_dir, "Smart App Control is on: installing Python 3.11 from python.org with winget")
            subprocess.run(["winget", "install", "--id=Python.Python.3.11", "-e", "--scope", "user",
                            "--accept-source-agreements", "--accept-package-agreements"],
                           capture_output=True, creationflags=_NO_WINDOW)
            py = signed_python()
        if not py:
            raise RuntimeError("Не удалось найти или установить Python 3.11 с python.org")
    if uv:
        tries = [[uv, "venv", str(new), "--python", py]] if py else [
            [uv, "venv", str(new), "--python", "3.11", "--python-preference", "only-managed"],
            [uv, "venv", str(new), "--python", "3.11"]]
    else:
        py = py or signed_python()
        if not py:
            raise RuntimeError("Нет ни uv, ни Python 3.11: запустите setup.bat")
        tries = [[py, "-m", "venv", str(new)]]
    for cmd in tries:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", creationflags=_NO_WINDOW)
        _log(data_dir, f"{' '.join(cmd[1:3])}: rc={res.returncode} {(res.stderr or '').strip()[-300:]}")
        if res.returncode == 0 and (new / "Scripts" / "python.exe").is_file():
            break
    base = _base_python(new)
    if not base:
        shutil.rmtree(new, ignore_errors=True)
        raise RuntimeError("Не удалось создать окружение Python 3.11")
    return base


def launch(app_dir: Path, data_dir: Path) -> bool:
    """Starts the move outside this environment; True when the app should quit now."""
    try:
        base = prepare(app_dir, data_dir)
    except Exception as e:
        _log(data_dir, f"prepare failed: {e}")
        _mark_failed(data_dir)
        return False
    cmd = [base, "-m", "app.envmove", "run", "--app-dir", str(app_dir), "--data-dir", str(data_dir),
           "--pid", str(os.getpid())]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, cwd=str(app_dir), creationflags=flags, close_fds=True)
    _log(data_dir, f"started the move with {base}")
    return True


def _mark_failed(data_dir: Path) -> None:
    try:
        (data_dir / "updates").mkdir(parents=True, exist_ok=True)
        (data_dir / "updates" / FAILED_MARK).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except OSError:
        pass


def _rename(src: Path, dst: Path) -> None:
    """A folder rename; files of a just closed program may stay locked a moment."""
    for attempt in range(20):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def move(app_dir: Path, data_dir: Path, report) -> None:
    """Installs the packages into .venv-new and puts it in the place of .venv (step 2)."""
    from app.pyenv import find_uv
    from app.updater import TORCH_INDEX, _preflight

    new, cur, old = app_dir / NEW_ENV, app_dir / ".venv", app_dir / OLD_ENV
    py = new / "Scripts" / "python.exe"
    if not py.is_file():
        raise RuntimeError("Новое окружение не создано")
    _preflight(app_dir, 9_000_000_000)  # the packages (~6 GB) next to the old environment
    report("Установка библиотек Python 3.11 (несколько гигабайт, обычно 5–15 минут)…")
    uv = find_uv()
    req = str(app_dir / "requirements.txt")
    cmd = ([uv, "pip", "install", "--python", str(py), "-r", req, "--require-hashes",
            "--index-strategy", "unsafe-best-match", "--extra-index-url", TORCH_INDEX] if uv else
           [str(py), "-m", "pip", "install", "-r", req, "--require-hashes", "--extra-index-url", TORCH_INDEX])
    if not uv:
        from app.pyenv import ensure_pip

        ensure_pip(str(py))
    with open(data_dir / "logs" / "update.log", "a", encoding="utf-8") as lf:
        res = subprocess.run(cmd, cwd=str(app_dir), stdout=lf, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError("Не удалось установить библиотеки (подробности в logs\\update.log)")
    report("Замена окружения…")
    shutil.rmtree(old, ignore_errors=True)
    _rename(cur, old)
    try:
        _rename(new, cur)
    except Exception:
        _rename(old, cur)  # the old one back in its place
        raise


def cleanup(app_dir: Path, data_dir: Path) -> None:
    """Step 3, on Python 3.11: the previous environment is not needed any more."""
    old = app_dir / OLD_ENV
    if sys.version_info[:2] < NEEDED:
        return
    (data_dir / "updates" / FAILED_MARK).unlink(missing_ok=True)
    if old.exists():
        threading.Thread(target=shutil.rmtree, args=(old,), kwargs={"ignore_errors": True},
                         daemon=True, name="envmove_cleanup").start()


def _main(argv) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["run", "prepare"])
    ap.add_argument("--app-dir", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--no-restart", action="store_true")  # update.bat: it starts the app itself
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # update.bat's window or a log in another code page
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    app_dir = Path(a.app_dir)
    if a.data_dir:
        data_dir = Path(a.data_dir)
    else:
        from app.paths import DATA_DIR

        data_dir = Path(DATA_DIR)

    if a.action == "prepare":  # update.bat: prints the Python 3.11 that runs the move
        try:
            print(prepare(app_dir, data_dir))
            return 0
        except Exception as e:
            print(f"ERROR {e}", file=sys.stderr)
            return 1

    from app.updater import _ProgressWindow, _wait_for_exit

    console = a.no_restart  # update.bat: messages in its window instead of the progress window
    ui = None if console else _ProgressWindow()
    ok, message = True, ""

    def report(text: str) -> None:
        if ui:
            ui.set(text)
        else:
            print("      " + text, flush=True)

    def work() -> None:
        nonlocal ok, message
        try:
            report("Переход на Python 3.11: ожидание закрытия VerbaScribe…")
            if a.pid and not _wait_for_exit(a.pid):
                raise RuntimeError("VerbaScribe не закрылся за две минуты")
            move(app_dir, data_dir, report)
            _log(data_dir, "the environment is on Python 3.11")
            report("Готово.")
        except Exception as e:
            ok, message = False, str(e)
            _log(data_dir, f"move failed: {e}")
            _mark_failed(data_dir)
            shutil.rmtree(app_dir / NEW_ENV, ignore_errors=True)
        finally:
            launcher = app_dir / "VerbaScribe.exe"
            if not a.no_restart and launcher.is_file():
                subprocess.Popen([str(launcher)], cwd=str(app_dir))
            if ui:
                ui.close(None if ok else f"Переход на Python 3.11 не выполнен: {message}\n"
                                         "Программа работает на прежнем окружении, новая попытка — через день.")

    if console or ui is None:
        work()
        if not ok:
            print(f"Переход на Python 3.11 не выполнен: {message}. Прежнее окружение на месте.", file=sys.stderr)
        return 0 if ok else 1
    threading.Thread(target=work, daemon=True).start()
    ui.run()  # until the work closes the window
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
