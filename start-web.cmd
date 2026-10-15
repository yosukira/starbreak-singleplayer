@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "python\python.exe" (
  echo Python runtime missing. Download and extract the complete Windows player ZIP.
  echo 缺少运行环境，请完整解压 Windows 玩家包，保留 python 文件夹。
  pause
  exit /b 1
)
set "PYTHONHOME="
set "PYTHONPATH="
"python\python.exe" tools\web_app.py %*
if errorlevel 1 pause
