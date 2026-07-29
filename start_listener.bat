@echo off
chcp 65001 >nul
cd /d "%~dp0rpa"

echo ===================================
echo    换图 RPA 监听器
echo ===================================
echo.

:: Find Python (ShadowBot Python has Playwright preinstalled)
set PYTHON=
if exist "C:\Program Files\ShadowBot\shadowbot-6.2.23\python\python.exe" (
    set PYTHON=C:\Program Files\ShadowBot\shadowbot-6.2.23\python\python.exe
) else if exist "C:\Program Files\ShadowBot\shadowbot-6.2.14\python\python.exe" (
    set PYTHON=C:\Program Files\ShadowBot\shadowbot-6.2.14\python\python.exe
) else if exist "C:\Users\s\AppData\Local\Programs\Python\Python311\python.exe" (
    set PYTHON=C:\Users\s\AppData\Local\Programs\Python\Python311\python.exe
    echo ⚠ Using Python 3.11 — make sure playwright is installed
    echo   pip install playwright ^&^& python -m playwright install chromium
) else (
    echo Python not found!
    pause
    exit /b 1
)

echo Python: %PYTHON%
echo Port: 8767
echo.

%PYTHON% swap_listener.py
pause
