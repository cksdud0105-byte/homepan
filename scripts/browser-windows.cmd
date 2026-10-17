@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup-windows.cmd first.
  exit /b 1
)
".venv\Scripts\python.exe" -m homepan.browser %*
exit /b %errorlevel%
