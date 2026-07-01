@echo off
cd /d "%~dp0"
if exist "%~dp0data\webview_profile\EBWebView\Default\Cache" rmdir /s /q "%~dp0data\webview_profile\EBWebView\Default\Cache"
if exist "%~dp0data\webview_profile\EBWebView\Default\Code Cache" rmdir /s /q "%~dp0data\webview_profile\EBWebView\Default\Code Cache"
"%~dp0.venv\Scripts\python.exe" run_app.py
pause
