@echo off
cd /d "%~dp0"

echo ==============================================================
echo   VerbaScribe - First-Run Environment Setup
echo ==============================================================

where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [0/2] Installing uv package manager...
    winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements >nul 2>nul
    set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
)

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [1/2] Creating Python 3.10 virtual environment via uv...
    rem Prefer a uv-managed Python: the Microsoft Store Python virtualizes AppData and hides files from other programs
    uv venv .venv --python 3.10 --python-preference only-managed || uv venv .venv --python 3.10
    echo [2/2] Installing dependencies via uv...
    rem requirements.txt is an exact lock with SHA-256 hashes (see requirements.in)
    uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt --require-hashes --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu124
    goto :done
)

echo [1/2] Creating Python virtual environment...
py -3.10 -m venv .venv 2>nul || python -m venv .venv
echo [2/2] Installing dependencies via pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt --require-hashes --extra-index-url https://download.pytorch.org/whl/cu124

:done
echo.
echo ==============================================================
echo   Setup complete! Launching VerbaScribe...
echo ==============================================================
