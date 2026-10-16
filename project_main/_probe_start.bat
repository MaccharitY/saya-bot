@echo off
cd /d "%~dp0.."
echo [cmd cwd] %CD%
wsl -d Ubuntu -e bash -lc "pwd; ls -l tools/start_saya.sh | cut -d' ' -f9-"
