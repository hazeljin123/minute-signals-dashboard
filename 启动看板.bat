@echo off
chcp 65001 >nul
title 分钟线信号看板 - 实时刷新
cd /d "%~dp0"

set PY=C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

echo ============================================
echo   分钟线买卖点信号看板
echo   实时刷新模式
echo ============================================
echo.
echo 本窗口需要保持开启。关闭 = 停止刷新。
echo.

REM 后台起服务
start "kanpan-server" /min "%PY%" serve.py --port 8765 --interval 10

REM 等服务起来
timeout /t 6 /nobreak >nul

echo 已在浏览器打开看板...
echo 地址：http://127.0.0.1:8765
echo.
echo 数据每 10 秒自动更新一次。
echo 停止服务请关闭标题为 kanpan-server 的窗口。
echo.

start "" "http://127.0.0.1:8765/dashboard.html"

timeout /t 5 /nobreak >nul
exit /b 0
