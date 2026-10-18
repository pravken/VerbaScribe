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
    uv venv .venv --python 3.10
    echo [2/2] Installing dependencies via uv...
    uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
    goto :done
)

echo [1/2] Creating Python virtual environment...
py -3.10 -m venv .venv 2>nul || python -m venv .venv
echo [2/2] Installing dependencies via pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124

:done
echo.
echo ==============================================================
echo   Setup complete! Launching VerbaScribe...
echo ==============================================================
