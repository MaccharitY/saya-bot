@echo off
chcp 65001 >nul
rem ============================================================
rem  saya_bot controller -- double-click this file.
rem
rem  Opens the control panel (tools/panel.py) inside WSL, right in
rem  this window: start / end / restart the bot, change settings.
rem  No cd, nothing to remember.
rem
rem  !! ASCII ONLY IN THIS FILE. cmd.exe misparses UTF-8 .bat files
rem     that contain non-ASCII characters: it splits lines in the
rem     middle and tries to run the tail as a command. That bit us
rem     once already. Every Chinese message comes from the Python
rem     side inside WSL. Line endings must be CRLF.
rem
rem  !! NO "for /f" IN THIS FILE. A backquoted `for /f` command
rem     SWALLOWS THE CONSOLE'S STDIN -- measured: with one, the
rem     panel received nothing and exited on EOF immediately.
rem     We do not need it: WSL inherits cmd's working directory,
rem     so "cd /d %~dp0" below is enough to put us in the repo root.
rem ============================================================
setlocal

rem WSL inherits this as the starting directory (that is the whole
rem point -- it saves the Windows-to-WSL path conversion entirely).
cd /d "%~dp0"

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

title saya_bot controller
wsl -d Ubuntu -e bash -lc "exec python3 tools/panel.py"

echo.
echo Controller exited.
pause
