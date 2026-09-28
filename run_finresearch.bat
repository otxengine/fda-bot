@echo off
REM Interactive launcher: opens the dashboard, and starts the scheduler too
REM UNLESS one is already running as the always-on background service (see
REM run_background.bat / install_background_task.ps1) — the app has exactly
REM one writer by design (plan's ADR-2), so this avoids a second one.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run this first:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e .
    exit /b 1
)

if not exist ".env" (
    echo No .env file found — copying .env.example to .env.
    echo Fill in your API keys in .env before the connectors will work.
    copy .env.example .env >nul
)

".venv\Scripts\python.exe" -c "from scheduler.main import is_scheduler_already_running; import sys; sys.exit(0 if is_scheduler_already_running() else 1)"
if %ERRORLEVEL% EQU 0 (
    echo A scheduler is already running in the background - not starting a second one.
) else (
    echo Starting scheduler (separate window)...
    start "finresearch-scheduler" cmd /k ".venv\Scripts\python.exe -m scheduler.main"
)

echo Starting dashboard...
".venv\Scripts\python.exe" -m streamlit run dashboard\app.py
