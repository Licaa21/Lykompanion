@echo off
cd /d "%~dp0.."
"%~dp0..\.venv\Scripts\python.exe" tools\ocr_debug.py %*
pause
