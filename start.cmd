@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0..\python\python.exe" (
  "%~dp0..\python\python.exe" tools\play_hulk.py --sbpe "%~dp0.." --sbpe-direct %*
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python tools\play_hulk.py --sbpe "%~dp0.." --sbpe-direct %*
  ) else (
    py -3 tools\play_hulk.py --sbpe "%~dp0.." --sbpe-direct %*
  )
)
if errorlevel 1 pause
endlocal
