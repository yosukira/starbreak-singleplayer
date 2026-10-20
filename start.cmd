@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "SIMULATOR_PYTHON_ARGS="
if exist "%~dp0python\python.exe" (
  set "SIMULATOR_PYTHON=%~dp0python\python.exe"
  goto run
)
if exist "%~dp0..\python\python.exe" (
  set "SIMULATOR_PYTHON=%~dp0..\python\python.exe"
  goto run
)
call :probe py -3
if not errorlevel 1 (
  set "SIMULATOR_PYTHON=py"
  set "SIMULATOR_PYTHON_ARGS=-3"
  goto run
)
call :probe python
if not errorlevel 1 (
  set "SIMULATOR_PYTHON=python"
  goto run
)
echo Python runtime missing. Download the Windows player ZIP from Releases,
echo extract the whole folder including python, and run start.cmd again.
echo 缺少 Python 运行环境。请下载 Releases 中的 Windows 玩家包，
echo 完整解压并保留 python 文件夹，然后重新运行 start.cmd。
echo https://github.com/yosukira/starbreak-singleplayer/releases/latest
set "SIMULATOR_EXIT=1"
goto finish

:probe
%~1 %~2 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
exit /b %errorlevel%

:run
if /i "%~1"=="--editor" goto editor
"%SIMULATOR_PYTHON%" %SIMULATOR_PYTHON_ARGS% tools\check_runtime.py
if errorlevel 1 (
  echo Runtime check failed. Re-extract the complete Windows player ZIP.
  echo 运行环境检查失败。请重新完整解压 Windows 玩家包。
  set "SIMULATOR_EXIT=1"
  goto finish
)
if /i "%~1"=="--check-runtime" (
  set "SIMULATOR_EXIT=0"
  goto finish
)
"%SIMULATOR_PYTHON%" %SIMULATOR_PYTHON_ARGS% tools\launch.py native --sbpe "%~dp0.." --sbpe-direct %*
set "SIMULATOR_EXIT=%errorlevel%"
goto finish

:editor
"%SIMULATOR_PYTHON%" %SIMULATOR_PYTHON_ARGS% -m tools.desktop_view --standalone --show
set "SIMULATOR_EXIT=%errorlevel%"

:finish
if not "%SIMULATOR_EXIT%"=="0" if /i not "%~1"=="--check-runtime" pause
exit /b %SIMULATOR_EXIT%
