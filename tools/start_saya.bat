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
rem
rem !! NO "for /f" IN THIS FILE. A backquoted `for /f` command SWALLOWS THE
rem    CONSOLE'S STDIN -- measured 2026-10-16: the bot started and printed its
rem    banner, but every command typed into it was ignored. We do not need it:
rem    WSL inherits cmd's working directory, so "cd /d %~dp0.." is enough.
rem    (This used to convert the path with wslpath in a `for /f`.)
rem ============================================================
setlocal

rem %~dp0 is tools\ ; the repo root is its parent.
rem WSL inherits this as its starting directory.
cd /d "%~dp0.."

where wsl >nul 2>nul
if errorlevel 1 (
    echo.
    echo WSL is not installed or not on PATH. Please check:
    echo   1. WSL and an Ubuntu distro are installed
    echo   2. "wsl -d Ubuntu" works in a Windows terminal
    echo.
    pause
    exit /b 1
)

title saya_bot
wsl -d Ubuntu -e bash -lc "exec bash tools/start_saya.sh --restart"

echo.
echo saya_bot exited.
pause
