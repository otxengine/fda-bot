@echo off
REM Always-on background service: runs the scheduler in a retry loop so it
REM survives a crash, and never as a second writer (scheduler/main.py's own
REM PID-file check refuses to start if one is already running - so it is
REM safe for this loop to just keep retrying rather than checking itself).
REM
REM Meant to be launched by Task Scheduler via run_background_hidden.vbs
REM (which hides the console window) - see install_background_task.ps1.
REM Logs to data\scheduler.log; check that file for status, or run
REM uninstall_background_task.ps1 to stop it running at logon.

cd /d "%~dp0"
if not exist "data" mkdir "data"

:loop
echo [%date% %time%] Starting scheduler... >> data\scheduler.log
".venv\Scripts\python.exe" -m scheduler.main >> data\scheduler.log 2>&1
echo [%date% %time%] Scheduler exited, restarting in 30s... >> data\scheduler.log
timeout /t 30 /nobreak >nul
goto loop
