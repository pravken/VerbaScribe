@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ==============================================================
echo   VerbaScribe - First-Run Environment Setup
echo ==============================================================

rem --- Smart App Control (Windows 11) blocks program files without a digital signature. The Python
rem --- that uv downloads is not signed, the one from python.org is: with Smart App Control on, the
rem --- environment is built on the python.org Python.
set "SAC=0"
for /f "tokens=3" %%v in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy" /v VerifiedAndReputablePolicyState 2^>nul ^| find "VerifiedAndReputablePolicyState"') do set "SAC=%%v"
if "%SAC%"=="0x0" set "SAC=0"
if "%SAC%"=="0x1" set "SAC=1"
if "%SAC%"=="0x2" set "SAC=2"

where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [0/2] Installing uv package manager...
    winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements >nul 2>nul
    set "PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
)

set "SIGNED_PY="
if not "%SAC%"=="0" call :find_signed_python
if not "%SAC%"=="0" if not defined SIGNED_PY (
    echo [0/2] Smart App Control is on: installing Python 3.10 from python.org ^(signed^)...
    winget install --id=Python.Python.3.10 -e --scope user --accept-source-agreements --accept-package-agreements >nul 2>nul
    call :find_signed_python
)
if not "%SAC%"=="0" call :sac_warning

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [1/2] Creating Python 3.10 virtual environment via uv...
    if defined SIGNED_PY (
        echo       Python: !SIGNED_PY!
        uv venv .venv --python "!SIGNED_PY!"
    ) else (
        rem Prefer a uv-managed Python: the Microsoft Store Python virtualizes AppData and hides files from other programs
        uv venv .venv --python 3.10 --python-preference only-managed || uv venv .venv --python 3.10
    )
    echo [2/2] Installing dependencies via uv...
    rem requirements.txt is an exact lock with SHA-256 hashes (see requirements.in)
    uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt --require-hashes --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu124
    goto :done
)

echo [1/2] Creating Python virtual environment...
if defined SIGNED_PY (
    "!SIGNED_PY!" -m venv .venv
) else (
    py -3.10 -m venv .venv 2>nul || python -m venv .venv
)
echo [2/2] Installing dependencies via pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt --require-hashes --extra-index-url https://download.pytorch.org/whl/cu124

:done
echo.
echo ==============================================================
echo   Setup complete. Launching VerbaScribe...
echo ==============================================================
goto :eof

rem --- Python 3.10 from python.org: the usual install folders, then the py launcher (never the
rem --- Microsoft Store Python in WindowsApps)
:find_signed_python
for %%p in ("%LOCALAPPDATA%\Programs\Python\Python310\python.exe" "%ProgramFiles%\Python310\python.exe") do (
    if exist "%%~p" (
        set "SIGNED_PY=%%~p"
        goto :eof
    )
)
for /f "delims=" %%p in ('py -3.10 -c "import sys; print(sys.executable)" 2^>nul') do (
    echo %%p | find /i "WindowsApps" >nul || set "SIGNED_PY=%%p"
)
goto :eof

:sac_warning
echo.
echo   ------------------------------------------------------------
echo   Smart App Control is on.
echo   It may block parts of Python libraries that have no digital
echo   signature ^(Windows shows "This app is partially blocked"^).
echo   If VerbaScribe does not start or a feature fails, turn it off:
echo   Windows Security - App ^& browser control - Smart App Control
echo   settings - Off. It cannot be turned back on without
echo   reinstalling Windows; Defender keeps protecting the PC.
echo   ------------------------------------------------------------
echo.
goto :eof
