# 测图看板一键启动脚本 - 双击运行
# 使用 Windows 本地 Python 启动 FastAPI 后端

Write-Host "=== 测图数据看板 ===" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $scriptDir "backend"

# Install dependencies if needed
Write-Host "检查依赖..." -ForegroundColor Yellow
try {
    python -c "import fastapi" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "安装 fastapi..." -ForegroundColor Yellow
        pip install fastapi uvicorn openpyxl requests -q
    }
} catch {
    Write-Host "安装依赖中..." -ForegroundColor Yellow
    pip install fastapi uvicorn openpyxl requests -q
}

Write-Host "启动后端 (http://127.0.0.1:8766)..." -ForegroundColor Green
Start-Process "http://127.0.0.1:8766"

Set-Location $backendDir
python main.py
pause
