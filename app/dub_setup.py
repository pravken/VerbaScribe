"""
Installs the optional Translate & Dub environment (.venv-dub) from the app, on explicit request.

Runs the same steps as setup_dub.bat: create the venv (uv if available, otherwise the standard
venv module), install requirements-dub.txt, install voxcpm without its demo and training deps,
then check that the packages really import. Only a successful check marks the environment as
ready, so an interrupted or failed install never enables the Dub button.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import APP_DIR
from app.pyenv import ensure_pip, find_uv as _find_uv

DUB_VENV = APP_DIR / ".venv-dub"
DUB_PYTHON = DUB_VENV / "Scripts" / "python.exe" if sys.platform == "win32" else DUB_VENV / "bin" / "python"
DUB_READY_MARKER = DUB_VENV / "verbascribe-dub-ready.txt"
# Both are locks with hashes (pip --require-hashes): a package changed on PyPI after the lock is refused
REQUIREMENTS = APP_DIR / "requirements-dub.txt"
VOXCPM_REQUIREMENTS = APP_DIR / "requirements-dub-voxcpm.txt"  # installed with --no-deps
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
DOWNLOAD_SIZE_GB = 5  # the environment takes 4.5 GB on disk (torch 2.11+cu126, VoxCPM2, Demucs)
MIN_FREE_GB = 10  # packages (~4.5 GB) plus room for the model weights (~5 GB) downloaded on the first dub

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
# perth silently sets its watermarker to None when an import fails, which only breaks at dub time
_VERIFY_CODE = (
    "import torch, demucs, voxcpm, perth, num2words; "
    "assert perth.PerthImplicitWatermarker is not None, 'perth watermarker failed to import'; "
    "print('cuda=' + str(torch.cuda.is_available()))"
)

_lock = threading.Lock()
_state: Dict[str, Any] = {"active": False, "progress": 0, "step": "", "error": "", "done": False, "logs": []}
_proc: Optional[subprocess.Popen] = None
_cancel = threading.Event()
_verified_once = False


def _log(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    with _lock:
        logs: List[str] = _state["logs"]
        logs.append(f"[{datetime.now():%H:%M:%S}] {line[:200]}")
        del logs[:-150]


def _set(**kw: Any) -> None:
    with _lock:
        _state.update(kw)


def _requirements_hash() -> str:
    try:
        # The same as app/updater.py _dub_requirements_hash
        return hashlib.sha256(b"".join(f.read_bytes() for f in (REQUIREMENTS, VOXCPM_REQUIREMENTS) if f.is_file())).hexdigest()
    except OSError:
        return ""


def _marked_requirements() -> Optional[str]:
    """The requirements hash the ready mark was made with; None without a mark or a hash in it."""
    try:
        m = re.search(r"^requirements=(\w+)", DUB_READY_MARKER.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def _has_voxcpm() -> bool:
    """The voice model's package is in .venv-dub (an environment made before 0.7.0 has Chatterbox)."""
    return any(DUB_VENV.glob("Lib/site-packages/voxcpm")) or any(DUB_VENV.glob("lib/python*/site-packages/voxcpm"))


def is_outdated() -> bool:
    """The environment was made for an older requirements-dub.txt (an update changed it): it needs
    its packages installed again before dubbing. A ready mark without the requirements it was made
    with (an old version, or a folder updated with git) counts as outdated when the voice model's
    package is missing: such an environment was made for Chatterbox."""
    if not DUB_READY_MARKER.is_file():
        return False
    marked = _marked_requirements()
    if marked is None:
        return not _has_voxcpm()
    return marked != _requirements_hash()


def _verify() -> bool:
    if not DUB_PYTHON.is_file():
        return False
    try:
        res = subprocess.run(
            [str(DUB_PYTHON), "-I", "-c", _VERIFY_CODE],
            capture_output=True, text=True, timeout=180, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if res.returncode != 0:
        tail = (res.stderr or "").strip().splitlines()[-3:]
        for line in tail:
            _log(f"check: {line}")
        return False
    # A re-check of an outdated environment keeps it outdated: the imports work, the versions are old
    req = _marked_requirements() if is_outdated() else _requirements_hash()
    DUB_READY_MARKER.write_text(
        f"Verified {datetime.now():%Y-%m-%d %H:%M:%S}: {res.stdout.strip()}\nrequirements={req}\n", encoding="utf-8"
    )
    return True


def is_installed() -> bool:
    """Ready = verified once. Environments made earlier by setup_dub.bat are checked lazily."""
    global _verified_once
    if DUB_READY_MARKER.is_file():
        return not is_outdated()
    with _lock:
        busy = _state["active"]
    if busy or _verified_once or not DUB_PYTHON.is_file():
        return False
    _verified_once = True  # one import check per app start for a venv without the marker
    return _verify()


def setup_status() -> Dict[str, Any]:
    with _lock:
        st = {**_state, "logs": list(_state["logs"])}
    try:
        free_gb = shutil.disk_usage(APP_DIR).free / 1e9
    except OSError:
        free_gb = None
    return {
        **st,
        "free_gb": None if free_gb is None else round(free_gb, 1),
        "min_free_gb": MIN_FREE_GB,
        "download_gb": DOWNLOAD_SIZE_GB,
        "venv": str(DUB_VENV),
        "uv": bool(_find_uv()),
        "outdated": is_outdated(),
    }


def _run(cmd: List[str], start_pct: int, end_pct: int, env: Dict[str, str]) -> int:
    """Runs one step, streams its output into the log and creeps the progress inside the step."""
    global _proc
    if _cancel.is_set():
        raise RuntimeError("Установка отменена")
    _log("$ " + " ".join(Path(cmd[0]).name if i == 0 else c for i, c in enumerate(cmd)))
    from app.shutdown import track

    proc = track(subprocess.Popen(
        cmd, cwd=str(APP_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    ))
    with _lock:
        _proc = proc
    pct = start_pct
    assert proc.stdout is not None
    for line in proc.stdout:
        _log(line)
        pct = min(end_pct - 1, pct + 1) if (end_pct - pct) > 1 else pct
        _set(progress=pct)
    rc = proc.wait()
    with _lock:
        _proc = None
    if _cancel.is_set():
        raise RuntimeError("Установка отменена")
    return rc


def _venv_python_version() -> tuple:
    """The Python of .venv-dub from its pyvenv.cfg ("version_info = 3.10.11"); (0,) when unknown."""
    try:
        for line in (DUB_VENV / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in ("version_info", "version"):
                return tuple(int(x) for x in value.strip().split(".")[:2])
    except (OSError, ValueError):
        pass
    return (0,)


def _has_chatterbox() -> bool:
    """chatterbox-tts in .venv-dub: an environment made for the voice model before 0.6.0."""
    return any(DUB_VENV.glob("Lib/site-packages/chatterbox*")) or any(DUB_VENV.glob("lib/python*/site-packages/chatterbox*"))


def _install() -> None:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [str(Path.home() / ".local" / "bin"), str(Path(env.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"), env.get("PATH", "")]
    )
    env["PYTHONUTF8"] = "1"
    uv = _find_uv()
    try:
        DUB_READY_MARKER.unlink(missing_ok=True)
        _set(step="venv", progress=2)
        if DUB_PYTHON.is_file() and _venv_python_version() < (3, 11):
            # Made on Python 3.10 before: the lock is for 3.11 now, the environment is made again
            _log("Окружение дубляжа на Python 3.10: создаётся заново на 3.11")
            shutil.rmtree(DUB_VENV, ignore_errors=True)
        elif DUB_PYTHON.is_file() and _has_chatterbox():
            # The voice model before 0.6.0: an install on top would keep its packages (pykakasi, GPL)
            _log("Окружение дубляжа с Chatterbox: создаётся заново для VoxCPM2")
            shutil.rmtree(DUB_VENV, ignore_errors=True)
        if not DUB_PYTHON.is_file():
            if uv:
                # A uv-managed Python avoids the Store Python's AppData virtualization
                if _run([uv, "venv", str(DUB_VENV), "--python", "3.11", "--python-preference", "only-managed"], 2, 8, env) != 0:
                    _run([uv, "venv", str(DUB_VENV), "--python", "3.11"], 2, 8, env)
            if not DUB_PYTHON.is_file():
                base = getattr(sys, "_base_executable", None) or sys.executable
                _run([base, "-m", "venv", str(DUB_VENV)], 2, 8, env)
            if not DUB_PYTHON.is_file():
                raise RuntimeError("Не удалось создать окружение .venv-dub (нужен Python 3.11 или uv)")

        _set(step="packages", progress=8)
        if uv:
            rc = _run(
                [uv, "pip", "install", "--python", str(DUB_PYTHON), "--require-hashes", "-r", str(REQUIREMENTS),
                 "--extra-index-url", TORCH_INDEX, "--index-strategy", "unsafe-best-match"],
                8, 85, env,
            )
        else:
            ensure_pip(str(DUB_PYTHON))
            _run([str(DUB_PYTHON), "-m", "pip", "install", "--upgrade", "pip"], 8, 10, env)
            rc = _run(
                [str(DUB_PYTHON), "-m", "pip", "install", "--require-hashes", "-r", str(REQUIREMENTS),
                 "--extra-index-url", TORCH_INDEX],
                10, 85, env,
            )
        if rc != 0:
            raise RuntimeError("Не удалось установить пакеты (подробности в журнале)")

        _set(step="voxcpm", progress=85)
        cmd = (
            [uv, "pip", "install", "--python", str(DUB_PYTHON), "--no-deps", "--require-hashes",
             "-r", str(VOXCPM_REQUIREMENTS)]
            if uv
            else [str(DUB_PYTHON), "-m", "pip", "install", "--no-deps", "--require-hashes", "-r", str(VOXCPM_REQUIREMENTS)]
        )
        if _run(cmd, 85, 92, env) != 0:
            raise RuntimeError("Не удалось установить voxcpm (подробности в журнале)")

        _set(step="verify", progress=93)
        _log("Проверка: импорт torch, demucs, voxcpm…")
        if not _verify():
            raise RuntimeError("Пакеты установлены, но не загружаются (подробности в журнале)")
        _log("Окружение дубляжа готово. Веса моделей (~5 ГБ) скачаются при первом дубляже.")
        _set(active=False, progress=100, step="done", done=True, error="")
    except Exception as e:
        _log(f"ОШИБКА: {e}")
        _set(active=False, error=str(e), done=False)


def start_setup() -> Dict[str, Any]:
    st = setup_status()
    if st["free_gb"] is not None and st["free_gb"] < MIN_FREE_GB:
        return {**st, "error": f"Недостаточно места на диске: свободно {st['free_gb']} ГБ, нужно не меньше {MIN_FREE_GB} ГБ"}
    if not REQUIREMENTS.is_file():
        return {**st, "error": f"Не найден файл {REQUIREMENTS.name}"}
    with _lock:
        if _state["active"]:
            return setup_status()
        _cancel.clear()
        _state.update(active=True, progress=1, step="start", error="", done=False, logs=[])
    _log(f"Установка окружения дубляжа в {DUB_VENV} (около {DOWNLOAD_SIZE_GB} ГБ)")
    threading.Thread(target=_install, daemon=True, name="dub_setup").start()
    return setup_status()


def cancel_setup() -> Dict[str, Any]:
    _cancel.set()
    with _lock:
        proc = _proc
    if proc and proc.poll() is None:
        if sys.platform == "win32":
            # uv/pip may spawn children: end the whole process tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=_NO_WINDOW)
        else:
            proc.kill()
    return setup_status()
