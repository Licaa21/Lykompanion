@echo off
cd /d "%~dp0"
set PORT=6692
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --reload
pause
