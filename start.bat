@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup.bat...
    call "%~dp0setup.bat"
)

".venv\Scripts\python.exe" "%~dp0run.py"
