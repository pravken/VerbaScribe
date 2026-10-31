@echo off
setlocal EnableDelayedExpansion

rem git may replace this very file while it runs: the work is done by a copy in %TEMP%
if not defined VS_UPDATE_DIR (
    set "VS_UPDATE_DIR=%~dp0"
    copy /y "%~f0" "%TEMP%\verbaliox-update.bat" >nul
    call "%TEMP%\verbaliox-update.bat" %*
    set "VS_RC=!errorlevel!"
    rem the block was read whole before it ran: the copy can go
    del "%TEMP%\verbaliox-update.bat" >nul 2>nul
    exit /b !VS_RC!
)
cd /d "%VS_UPDATE_DIR%"

rem Updates Verbaliox from GitHub with git (a folder cloned with git, next to Verbaliox.exe).
rem   update.bat            - the branch the folder is on now (main when none)
rem   update.bat <branch>   - switches to that branch, e.g. update.bat main
rem Files changed in the folder (by the in-app updater or by hand) are put aside with git stash,
rem not deleted: "git stash list" shows them. models\, .venv\ and the data are not touched.

echo ==============================================================
echo   Verbaliox - Update from GitHub
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

tasklist /fi "imagename eq Verbaliox.exe" 2>nul | find /i "Verbaliox.exe" >nul
if not errorlevel 1 (
    echo Verbaliox is running. Close it ^(also in the tray^), then press any key.
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
        rem An environment made on Python 3.10 is moved to 3.11 (app/envmove.py): a new .venv is
        rem made next to it and replaces it only when every package is installed
        ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
        if errorlevel 1 goto :move_env
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

:packages_done
echo.
echo Done: branch %BRANCH%, now at:
git log -1 --oneline
echo.
choice /m "Start Verbaliox now"
if not errorlevel 2 if exist "Verbaliox.exe" start "" "%VS_UPDATE_DIR%Verbaliox.exe"
exit /b 0

:move_env
echo       Moving the Python environment to Python 3.11 ^(several GB, once^)...
set "BASEPY="
for /f "delims=" %%p in ('".venv\Scripts\python.exe" -m app.envmove prepare') do set "BASEPY=%%p"
if not defined BASEPY (
    echo Could not create the Python 3.11 environment: see logs\update.log in the data folder.
    goto :fail
)
"%BASEPY%" -m app.envmove run --no-restart
if errorlevel 1 (
    echo The previous environment is kept: Verbaliox still works as before.
    goto :fail
)
goto :packages_done

:fail
echo.
echo Update did not finish.
pause
exit /b 1
