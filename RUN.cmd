@echo off
cd /d "%~dp0"
if exist "%~dp0data\webview_profile\EBWebView" rmdir /s /q "%~dp0data\webview_profile\EBWebView"
"%~dp0.venv\Scripts\python.exe" run_app.py
pause
