@echo off
chcp 65001 >nul
title 分钟线信号看板 - 刷新中
cd /d "%~dp0"

echo ============================================
echo   分钟线买卖点信号看板 - 数据刷新
echo ============================================
echo.

set PY=C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

"%PY%" build_dashboard.py

if errorlevel 1 (
  echo.
  echo [错误] 刷新失败，请检查网络连接。
  pause
  exit /b 1
)

echo.
echo 正在打开看板...
start "" "dashboard.html"
exit /b 0
