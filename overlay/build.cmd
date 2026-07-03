@echo off
REM Build the Lykompanion native overlay (single translation unit, MSVC).
REM Requires VS Build Tools with the C++ workload. Run from a "x64 Native Tools
REM Command Prompt", or this script will try to locate and load vcvars64.bat.

setlocal
cd /d "%~dp0"

REM A stray double-quote anywhere in PATH makes vcvars64.bat abort with a cryptic
REM "... was unexpected at this time." Quotes are never valid inside PATH, so
REM strip them here so a corrupted machine PATH can't break the build.
set "PATH=%PATH:"=%"

where cl.exe >nul 2>nul
if %ERRORLEVEL%==0 goto :build

REM cl.exe not on PATH — try to locate a VS installation via vswhere.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
    echo [build] cl.exe not found and vswhere.exe missing.
    echo         Open a "x64 Native Tools Command Prompt for VS" and rerun build.cmd.
    exit /b 1
)

for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSPATH=%%i"

if not defined VSPATH (
    echo [build] No VS installation with the C++ toolset found.
    exit /b 1
)

REM Redirect stderr too: vcvars64.bat internally calls a bareword `vswhere.exe`
REM and prints a harmless "not recognized" line when the Installer dir is off PATH.
call "%VSPATH%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [build] Failed to initialize the MSVC environment.
    exit /b 1
)

:build
REM /O2 optimized, /EHsc C++ exceptions, /std:c++17, GUI subsystem (no console).
cl /nologo /O2 /EHsc /std:c++17 /W3 ^
    overlay.cpp ^
    /link /SUBSYSTEM:WINDOWS ^
    user32.lib gdi32.lib d2d1.lib dwrite.lib shell32.lib ^
    /OUT:Lykompanion-overlay.exe

if %ERRORLEVEL%==0 (
    echo [build] OK -^> overlay\Lykompanion-overlay.exe
    del /q overlay.obj >nul 2>nul
) else (
    echo [build] FAILED
    exit /b 1
)
endlocal
