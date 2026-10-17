@echo off
REM ===========================================================================
REM  hermes-trading - launch the worker + dashboard (+ Discord bot if configured),
REM  then open the browser. A few small windows will open and must stay open while
REM  you use the app. To stop the app, close those windows.
REM
REM  Safe to double-click again at any time: parts that are already running are
REM  left alone (never a second copy), only missing parts are started, and the
REM  dashboard opens in your browser. Each part also refuses to run twice on its own.
REM ===========================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Not set up yet. Double-click  setup.bat  first.
  echo.
  pause
  exit /b 1
)

set "PY=.venv\Scripts\python"
set STARTED=0

REM --- worker ---------------------------------------------------------------
"%PY%" -m hermes_trading.single_instance running worker
if errorlevel 1 (
  echo Starting the trading worker...
  start "hermes worker" "%PY%" -m hermes_trading.run
  set STARTED=1
) else (
  echo   Trading worker is already running - leaving it alone.
)

REM --- dashboard ------------------------------------------------------------
"%PY%" -m hermes_trading.single_instance running dashboard
if errorlevel 1 (
  echo Starting the dashboard...
  start "hermes dashboard" "%PY%" -m uvicorn dashboard.app:app --port 8000
  set STARTED=1
) else (
  echo   Dashboard is already running - leaving it alone.
)

REM --- Discord approvals bot - only if a token is set in .env -----------------
findstr /b /r "DISCORD_BOT_TOKEN=." .env >nul 2>&1
if errorlevel 1 (
  echo   Discord not configured - skipping the bot ^(set DISCORD_BOT_TOKEN in .env to enable^).
) else (
  "%PY%" -m hermes_trading.single_instance running discord_bot
  if errorlevel 1 (
    echo Starting the Discord approvals bot...
    start "hermes discord bot" "%PY%" -m hermes_trading.discord_bot
    set STARTED=1
  ) else (
    echo   Discord bot is already running - leaving it alone.
  )
)

echo Opening the dashboard in your browser...
REM brief pause so a freshly started dashboard is up before the browser opens (ping
REM works even when stdin is redirected, unlike timeout).
if "%STARTED%"=="1" ping -n 5 127.0.0.1 >nul
start "" "http://localhost:8000"

echo.
echo   The app is running. Keep its windows open.
echo   Dashboard: http://localhost:8000
echo   Close this window any time - it is not needed once the app is up.
echo.
