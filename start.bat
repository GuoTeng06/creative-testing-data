@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

echo ===================================
echo    测图数据看板
echo ===================================
echo.

:: Find Python
set PYTHON=
if exist "C:\Progra~1\ShadowBot\shadowbot-6.2.14\python\python.exe" (
    set PYTHON=C:\Progra~1\ShadowBot\shadowbot-6.2.14\python\python.exe
    echo Using ShadowBot Python
) else if exist "C:\Users\s\AppData\Local\Programs\Python\Python311\python.exe" (
    set PYTHON=C:\Users\s\AppData\Local\Programs\Python\Python311\python.exe
    echo Using Python 3.11
) else (
    echo Python not found! Please install Python or check ShadowBot path.
    pause
    exit /b 1
)

echo Python: %PYTHON%
echo.

:: Install deps if needed
%PYTHON% -c "import fastapi" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Installing dependencies...
    %PYTHON% -m pip install fastapi uvicorn openpyxl requests -q
    echo Done.
)

echo Starting server on http://127.0.0.1:8766
echo.
echo Open this URL in your browser: http://127.0.0.1:8766
echo Press Ctrl+C to stop the server
echo.

start "" http://127.0.0.1:8766
%PYTHON% main.py
pause
