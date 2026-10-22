@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" desktop_worker.py --restore
) else (
  python desktop_worker.py --restore
)
pause
