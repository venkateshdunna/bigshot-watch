@echo off
REM One-time setup: installs dependencies and makes the India watcher start
REM automatically (hidden) every time you log in to Windows.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install it from https://www.python.org/downloads/ ^(tick "Add to PATH"^) and run this again.
  pause
  exit /b 1
)

python -m pip install --upgrade -r requirements.txt
if not exist config.yaml copy config.example.yaml config.yaml >nul

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo Set s = CreateObject("WScript.Shell") > "%STARTUP%\BigShotWatch.vbs"
echo s.CurrentDirectory = "%~dp0" >> "%STARTUP%\BigShotWatch.vbs"
echo s.Run "cmd /c pythonw watcher.py --feeds india >> watcher.log 2>&1", 0, False >> "%STARTUP%\BigShotWatch.vbs"

echo.
echo Done. Next:
echo   1. Edit config.yaml (Telegram token, chat id, watchlist)
echo   2. Run test_windows.bat to check everything works
echo   3. Restart Windows, or double-click "%STARTUP%\BigShotWatch.vbs" to start now
pause
