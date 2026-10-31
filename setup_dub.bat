@echo off
cd /d "%~dp0"

echo ==============================================================
echo   Verbaliox - optional Translate ^& Dub environment (.venv-dub)
echo   VoxCPM2 TTS (Apache-2.0) + Demucs (MIT), ~6 GB
echo ==============================================================

set "DUBPY=.venv-dub\Scripts\python.exe"
set "TORCH_INDEX=https://download.pytorch.org/whl/cu126"

rem An environment made on Python 3.10 before: the lock is for 3.11 now, it is made again
if exist "%DUBPY%" (
    "%DUBPY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || rmdir /s /q .venv-dub
)
rem An environment made for Chatterbox (before 0.6.0): made again, so none of its packages stay
if exist ".venv-dub\Lib\site-packages\chatterbox" rmdir /s /q .venv-dub
if not exist "%DUBPY%" (
    where uv >nul 2>nul && (uv venv .venv-dub --python 3.11 --python-preference only-managed || uv venv .venv-dub --python 3.11)
)
if not exist "%DUBPY%" (
    py -3.11 -m venv .venv-dub
)
if not exist "%DUBPY%" (
    echo Could not create .venv-dub. Install Python 3.11 or run setup.bat first.
    exit /b 1
)

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    uv pip install --python "%DUBPY%" --require-hashes -r requirements-dub.txt --extra-index-url %TORCH_INDEX% --index-strategy unsafe-best-match && uv pip install --python "%DUBPY%" --no-deps --require-hashes -r requirements-dub-voxcpm.txt && goto :done
)
"%DUBPY%" -m pip install --require-hashes -r requirements-dub.txt --extra-index-url %TORCH_INDEX%
"%DUBPY%" -m pip install --no-deps --require-hashes -r requirements-dub-voxcpm.txt

:done
echo.
echo Done. Model weights (~5 GB) are downloaded on the first dubbing job into models\dub.
