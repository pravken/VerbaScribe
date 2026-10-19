@echo off
cd /d "%~dp0"

echo ==============================================================
echo   VerbaScribe - optional Translate ^& Dub environment (.venv-dub)
echo   Chatterbox Multilingual TTS (MIT) + Demucs (MIT), ~6 GB
echo ==============================================================

set "DUBPY=.venv-dub\Scripts\python.exe"
set "TORCH_INDEX=https://download.pytorch.org/whl/cu124"

if not exist "%DUBPY%" (
    where uv >nul 2>nul && uv venv .venv-dub --python 3.10
)
if not exist "%DUBPY%" (
    py -3.10 -m venv .venv-dub
)
if not exist "%DUBPY%" (
    echo Could not create .venv-dub. Install Python 3.10 or run setup.bat first.
    exit /b 1
)

where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    uv pip install --python "%DUBPY%" -r requirements-dub.txt --extra-index-url %TORCH_INDEX% --index-strategy unsafe-best-match && uv pip install --python "%DUBPY%" --no-deps chatterbox-tts==0.1.7 && goto :done
)
"%DUBPY%" -m pip install -r requirements-dub.txt --extra-index-url %TORCH_INDEX%
"%DUBPY%" -m pip install --no-deps chatterbox-tts==0.1.7

:done
echo.
echo Done. Model weights (~3 GB) are downloaded on the first dubbing job into models\dub.
