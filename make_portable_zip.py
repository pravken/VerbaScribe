import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SETUP_BAT = """@echo off
cd /d "%~dp0"

echo ==============================================================
echo   VerbaScribe - First-Run Environment Setup
echo ==============================================================

where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [0/2] Installing uv package manager...
    winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements >nul 2>nul
    set "PATH=%USERPROFILE%\\.local\\bin;%LOCALAPPDATA%\\Microsoft\\WinGet\\Links;%PATH%"
)

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [1/2] Creating Python 3.10 virtual environment via uv...
    uv venv .venv --python 3.10
    echo [2/2] Installing dependencies via uv...
    uv pip install --python ".venv\\Scripts\\python.exe" -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
    goto :done
)

echo [1/2] Creating Python virtual environment...
py -3.10 -m venv .venv 2>nul || python -m venv .venv
echo [2/2] Installing dependencies via pip...
".venv\\Scripts\\python.exe" -m pip install --upgrade pip
".venv\\Scripts\\python.exe" -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124

:done
echo.
echo ==============================================================
echo   Setup complete! Launching VerbaScribe...
echo ==============================================================
"""


def write_setup_bat() -> None:
    setup_path = BASE_DIR / "setup.bat"
    with open(setup_path, "wb") as f:
        f.write(SETUP_BAT.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))


def build_portable_zip() -> Path:
    write_setup_bat()
    zip_path = BASE_DIR / "VerbaScribe_Portable.zip"

    include_files = [
        "VerbaScribe.exe",
        "run.py",
        "setup.bat",
        "start.bat",
        "requirements.txt",
        "LICENSE",
        "README.md",
        ".gitignore",
        "build_exe.py",
        "assets/icon.ico",
        "static/index.html",
    ]
    app_files = sorted((BASE_DIR / "app").glob("*.py"))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in include_files:
            p = BASE_DIR / rel
            if p.exists():
                zf.write(p, arcname=f"VerbaScribe/{rel}")
        for p in app_files:
            zf.write(p, arcname=f"VerbaScribe/app/{p.name}")

    print(f"[OK] Portable archive created: {zip_path} ({zip_path.stat().st_size / 1024:.1f} KB)")
    return zip_path


if __name__ == "__main__":
    build_portable_zip()
