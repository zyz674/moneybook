@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONPATH=%~dp0src
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 python，请先安装 Python 3.9+ 并勾选 Add to PATH
  pause
  exit /b 1
)
echo 正在启动记账本…（关闭本窗口即停止服务）
python -m moneybook %*
pause
