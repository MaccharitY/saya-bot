@echo off
chcp 65001 >nul
rem One-click start / restart for saya_bot. Double-click this file.
rem
rem It only launches tools/start_saya.sh inside WSL; everything real happens there.
rem
rem --restart is intentional: double-clicking this button means
rem "give me a running bot". If one is already running it is stopped first and
rem restarted on the current code; if not, it just starts.
rem (Without the flag the script only prints a notice and exits, which is
rem useless for double-clicking.)
rem
rem !! ASCII ONLY IN THIS FILE. Do not add non-ASCII characters -- cmd.exe
rem    misparses UTF-8 .bat files that contain them, splitting lines in the
rem    middle and trying to run the tail as a command. All Chinese messages
rem    come from the shell script on the WSL side.
setlocal

set "LOCALDIR=%~dp0.."
set "WSLDIR="
for /f "usebackq delims=" %%i in (`wsl -d Ubuntu wslpath -a "%LOCALDIR%" 2^>nul`) do set "WSLDIR=%%i"

if "%WSLDIR%"=="" (
    echo.
    echo Cannot resolve the path inside WSL. Please check:
    echo   1. WSL and an Ubuntu distro are installed
    echo   2. "wsl -d Ubuntu" works in a Windows terminal
    echo.
    pause
    exit /b 1
)

title saya_bot
wsl -d Ubuntu -e bash -lc "bash '%WSLDIR%/tools/start_saya.sh' --restart"

echo.
echo saya_bot exited.
pause
