@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set PORT=8787
if not "%~1"=="" set PORT=%~1
set FOUND=0
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
  echo 结束进程 PID=%%P
  taskkill /PID %%P /F >nul 2>nul
  set FOUND=1
)
if "!FOUND!"=="0" echo 端口 %PORT% 上没有发现运行中的记账本
timeout /t 2 >nul
