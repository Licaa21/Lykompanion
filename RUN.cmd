@echo off
cd /d "%~dp0"
if exist "%~dp0data\webview_profile\EBWebView\Default\Cache" rmdir /s /q "%~dp0data\webview_profile\EBWebView\Default\Cache"
if exist "%~dp0data\webview_profile\EBWebView\Default\Code Cache" rmdir /s /q "%~dp0data\webview_profile\EBWebView\Default\Code Cache"
echo [run] Building native overlay...
call "%~dp0overlay\build.cmd"
if errorlevel 1 echo [run] Overlay build failed - launching with the existing overlay exe.
"%~dp0.venv\Scripts\python.exe" run_app.py
pause
