@echo off
setlocal
py -3 "%~dp0setup_windows.py"
if errorlevel 1 (
  echo Setup failed. Check the message above. Python 3.11 or newer is required.
  pause
  exit /b 1
)
pause
