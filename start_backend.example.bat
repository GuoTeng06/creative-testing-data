@echo off
REM 复制此文件为 start_backend.bat 并填入真实凭据
set MYSQL_HOST=192.168.16.38
set MYSQL_PORT=3306
set MYSQL_USER=root
set MYSQL_PASSWORD=your_password_here
set MYSQL_DATABASE=creative testing data
echo Starting cetu-dashboard backend...
cd /d C:\Users\s\Desktop\cetu-dashboard\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8766
pause
