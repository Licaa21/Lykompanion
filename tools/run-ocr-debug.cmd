@echo off
mode con: cols=200 lines=9999
cd /d "%~dp0.."
"%~dp0..\.venv\Scripts\python.exe" tools\ocr_debug.py %*
pause
