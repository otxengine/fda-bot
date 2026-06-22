@echo off
title FDA Options Scanner
cd /d "%~dp0"

echo Starting FDA Options Scanner...
echo Dashboard: http://localhost:8000
echo Press Ctrl+C to stop.
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
