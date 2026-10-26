@echo off
setlocal EnableDelayedExpansion

rem git may replace this very file while it runs: the work is done by a copy in %TEMP%
if not defined VS_UPDATE_DIR (
    set "VS_UPDATE_DIR=%~dp0"
    copy /y "%~f0" "%TEMP%\verbascribe-update.bat" >nul
    call "%TEMP%\verbascribe-update.bat" %*
    set "VS_RC=!errorlevel!"
    rem the block was read whole before it ran: the copy can go
    del "%TEMP%\verbascribe-update.bat" >nul 2>nul
    exit /b !VS_RC!
)
cd /d "%VS_UPDATE_DIR%"

rem Updates VerbaScribe from GitHub with git (a folder cloned with git, next to VerbaScribe.exe).
rem   update.bat            - the branch the folder is on now (main when none)
rem   update.bat <branch>   - switches to that branch, e.g. update.bat main
rem Files changed in the folder (by the in-app updater or by hand) are put aside with git stash,
rem not deleted: "git stash list" shows them. models\, .venv\ and the data are not touched.

echo ==============================================================
echo   VerbaScribe - Update from GitHub
echo ==============================================================

where git >nul 2>nul
if errorlevel 1 (
    echo git is not installed: https://git-scm.com/download/win
    goto :fail
)
if not exist ".git" (
    echo This folder was not cloned with git, so it cannot be updated this way.
    echo Use Settings - Updates in the app, or download the release from GitHub.
    goto :fail
)

tasklist /fi "imagename eq VerbaScribe.exe" 2>nul | find /i "VerbaScribe.exe" >nul
if not errorlevel 1 (
    echo VerbaScribe is running. Close it ^(also in the tray^), then press any key.
    pause >nul
)

set "BRANCH=%~1"
if "%BRANCH%"=="" for /f "delims=" %%b in ('git branch --show-current') do set "BRANCH=%%b"
if "%BRANCH%"=="" set "BRANCH=main"
for /f "delims=" %%h in ('git rev-parse HEAD') do set "OLD=%%h"

echo [1/4] Downloading branch %BRANCH%...
git fetch origin %BRANCH%
if errorlevel 1 (
    echo Could not download the branch "%BRANCH%": check its name and the internet connection.
    goto :fail
)

echo [2/4] Putting local changes aside...
set "DIRTY="
for /f "delims=" %%s in ('git status --porcelain') do set "DIRTY=1"
if defined DIRTY (
    git stash push --include-untracked -m "update.bat %DATE% %TIME%"
    if errorlevel 1 goto :fail
    echo       Saved: "git stash list" shows them, "git stash pop" brings them back.
) else (
    echo       None.
)

echo [3/4] Switching to %BRANCH%...
git show-ref --verify --quiet refs/heads/%BRANCH%
if errorlevel 1 (
    git checkout -b %BRANCH% --track origin/%BRANCH%
) else (
    git checkout %BRANCH%
)
if errorlevel 1 goto :fail
git merge --ff-only origin/%BRANCH%
if errorlevel 1 (
    echo The local branch has its own commits: it is not changed. Ask for help before going on.
    goto :fail
)

echo [4/4] Checking the Python packages...
for /f "delims=" %%h in ('git rev-parse HEAD') do set "NEW=%%h"
git diff --quiet %OLD% %NEW% -- requirements.txt
if errorlevel 1 (
    if not exist ".venv\Scripts\python.exe" (
        echo       No .venv yet: running setup.bat...
        call "%~dp0setup.bat"
    ) else (
        echo       The package list changed: installing...
        where uv >nul 2>nul
        if not errorlevel 1 (
            uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt --require-hashes --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu126
        ) else (
            ".venv\Scripts\python.exe" -m pip install -r requirements.txt --require-hashes --extra-index-url https://download.pytorch.org/whl/cu126
        )
        if errorlevel 1 (
            echo Installing the packages failed: see the messages above.
            goto :fail
        )
    )
) else (
    echo       Unchanged.
)

echo.
echo Done: branch %BRANCH%, now at:
git log -1 --oneline
echo.
choice /m "Start VerbaScribe now"
if not errorlevel 2 if exist "VerbaScribe.exe" start "" "%VS_UPDATE_DIR%VerbaScribe.exe"
exit /b 0

:fail
echo.
echo Update did not finish.
pause
exit /b 1
