@echo off
cd /d "%~dp0"
if exist "dist-vue\AI Link\AI Link.exe" (
  start "" "dist-vue\AI Link\AI Link.exe"
  exit /b 0
)
if not exist "desktop-app\node_modules\electron" (
  echo Read README.md and install project dependencies first.
  pause
  exit /b 1
)
cd desktop-app
call npm.cmd run build
if errorlevel 1 exit /b 1
call npm.cmd start
