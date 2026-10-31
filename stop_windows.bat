@echo off
REM Stops the background watcher and removes it from startup.
taskkill /f /im pythonw.exe >nul 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\BigShotWatch.vbs" 2>nul
echo Stopped and removed from startup.
pause
