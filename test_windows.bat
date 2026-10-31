@echo off
REM Checks Telegram, then does one dry pass over every feed (prints, doesn't send).
cd /d "%~dp0"
echo === Sending a Telegram test message ===
python watcher.py --test-telegram
echo.
echo === One pass over all feeds (printed here, not sent) ===
python watcher.py --once --dry-run --no-seed --state test_state.json
del test_state.json 2>nul
echo.
echo If you saw "error" lines above, copy them to Claude.
pause
