@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\Program Files\ShadowBot\shadowbot-6.2.23\python\python.exe" "%~dp0mysql_proxy.py"
pause
