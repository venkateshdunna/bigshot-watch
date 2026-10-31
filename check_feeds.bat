@echo off
REM Quick health check: one line per feed, no alert text.
cd /d "%~dp0"
python watcher.py --once --dry-run --no-seed --state check_state.json > check_log.txt 2>&1
del check_state.json 2>nul
echo.
echo ===== FEED STATUS =====
findstr /C:"] OK" /C:"error" check_log.txt
echo =======================
echo (Full output saved in check_log.txt)
pause
