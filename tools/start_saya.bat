@echo off
rem 一键启动 saya_bot —— 双击这个文件即可（Windows 侧）。
rem
rem 它只是把 WSL 里的 tools/start_saya.sh 拉起来；
rem 真正的事情都在那里面做。
setlocal

set "LOCALDIR=%~dp0.."
for /f "usebackq delims=" %%i in (`wsl -d Ubuntu wslpath -a "%LOCALDIR%" 2^>nul`) do set "WSLDIR=%%i"

if "%WSLDIR%"=="" (
    echo.
    echo 找不到 WSL 里的路径。请确认：
    echo   1. 装了 WSL 与 Ubuntu 发行版
    echo   2. 在 Windows 终端里能跑 wsl -d Ubuntu
    echo.
    pause
    exit /b 1
)

title saya_bot
wsl -d Ubuntu -e bash -lc "bash '%WSLDIR%/tools/start_saya.sh'"

echo.
echo saya_bot 已退出。
pause
