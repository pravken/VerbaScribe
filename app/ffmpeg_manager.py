"""
Locates FFmpeg and, only when the user asks for it, downloads a pinned Windows build.

Lookup order: the copy downloaded by VerbaScribe (<data>/bin), then FFmpeg on PATH.
The download is a fixed release of the "essentials" build by Gyan Doshi (the build linked from
ffmpeg.org for Windows), fetched from its GitHub releases and checked against a SHA-256 hash that
is pinned below. Only ffmpeg.exe, ffprobe.exe and the license are extracted.
"""

import hashlib
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from app.paths import BIN_DIR

FFMPEG_VERSION = "9.0.2"
FFMPEG_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/"
    f"{FFMPEG_VERSION}/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
)
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
FFMPEG_SIZE = 114768076

_EXE = ".exe" if sys.platform == "win32" else ""
_state_lock = threading.Lock()
_state: Dict[str, Any] = {"active": False, "progress": 0, "downloaded_mb": 0.0, "error": "", "done": False}


def _local(name: str) -> Path:
    return BIN_DIR / f"{name}{_EXE}"


def find_tool(name: str) -> Optional[str]:
    """Absolute path of ffmpeg/ffprobe, or None if not installed."""
    local = _local(name)
    if local.is_file():
        return str(local)
    return shutil.which(name)


def tool_cmd(name: str) -> str:
    """Executable to put into a subprocess command line (falls back to the bare name)."""
    return find_tool(name) or name


def ffmpeg_status() -> Dict[str, Any]:
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    source = None
    if ffmpeg:
        source = "downloaded" if Path(ffmpeg) == _local("ffmpeg") else "system"
    with _state_lock:
        download = dict(_state)
    return {
        "available": bool(ffmpeg and ffprobe),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "source": source,
        "can_download": sys.platform == "win32",
        "download_info": {"version": FFMPEG_VERSION, "url": FFMPEG_URL, "sha256": FFMPEG_SHA256, "size_mb": round(FFMPEG_SIZE / 1e6)},
        "download": download,
    }


def _set(**kw: Any) -> None:
    with _state_lock:
        _state.update(kw)


def _download_and_install() -> None:
    import httpx

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    part = BIN_DIR / "ffmpeg-download.zip.part"
    try:
        digest = hashlib.sha256()
        done = 0
        with httpx.stream("GET", FFMPEG_URL, follow_redirects=True, timeout=60.0) as resp:
            resp.raise_for_status()
            with part.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    _set(progress=min(99, int(done * 100 / FFMPEG_SIZE)), downloaded_mb=round(done / 1e6, 1))
        if digest.hexdigest() != FFMPEG_SHA256:
            raise RuntimeError("Контрольная сумма архива FFmpeg не совпала — файл отклонён")

        wanted = {f"ffmpeg{_EXE}": _local("ffmpeg"), f"ffprobe{_EXE}": _local("ffprobe"), "LICENSE": BIN_DIR / "FFMPEG-LICENSE.txt"}
        found = set()
        with zipfile.ZipFile(part) as zf:
            for info in zf.infolist():
                name = info.filename.rsplit("/", 1)[-1]
                parent = info.filename.rsplit("/", 2)[-2] if info.filename.count("/") >= 1 else ""
                if name in wanted and (name == "LICENSE" or parent == "bin") and name not in found:
                    # Write to fixed names only: archive paths never decide where files go
                    tmp = wanted[name].with_suffix(wanted[name].suffix + ".tmp")
                    with zf.open(info) as src, tmp.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    tmp.replace(wanted[name])
                    found.add(name)
        if not {f"ffmpeg{_EXE}", f"ffprobe{_EXE}"} <= found:
            raise RuntimeError("В архиве не найдены ffmpeg.exe и ffprobe.exe")
        _set(active=False, progress=100, done=True, error="")
    except Exception as e:
        _set(active=False, error=str(e))
    finally:
        part.unlink(missing_ok=True)


def start_ffmpeg_download() -> Dict[str, Any]:
    if sys.platform != "win32":
        return {**ffmpeg_status(), "error": "Автоматическая установка доступна только в Windows"}
    with _state_lock:
        if not _state["active"]:
            _state.update(active=True, progress=0, downloaded_mb=0.0, error="", done=False)
            threading.Thread(target=_download_and_install, daemon=True, name="ffmpeg_download").start()
    return ffmpeg_status()
