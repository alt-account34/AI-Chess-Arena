@echo off
rem AI Chess Arena launcher: creates the virtual environment on first run,
rem then starts the server and opens http://localhost:8000
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" -m arena %*
goto :eof

:error
echo.
echo Setup failed. See SETUP.md.
pause
