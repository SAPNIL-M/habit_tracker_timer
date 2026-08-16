@echo off
setlocal
cd /d "%~dp0"
title Profile Stopwatch

echo ==============================================
echo   Profile Stopwatch
echo ==============================================
echo.

rem --- already running? ---
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [stopwatch] The server is ALREADY running.
  echo [stopwatch] Just open  http://127.0.0.1:8765  in your sapnilm profile.
  echo.
  pause
  exit /b 0
)

rem --- is the venv usable? (probe it instead of checking existence) ---
".venv\Scripts\python.exe" --version >nul 2>&1
if errorlevel 1 (
  echo [stopwatch] Setting up Python environment...
  where python >nul 2>nul
  if errorlevel 1 (
    echo [stopwatch] Python was not found. Install Python 3.10+ from https://python.org
    echo [stopwatch] and re-run start.bat. Re-open the terminal after installing.
    echo.
    pause
    exit /b 1
  )
  python -m venv --clear .venv
  if errorlevel 1 goto :err
  echo [stopwatch] Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :err
  echo [stopwatch] Environment ready.
)

echo [stopwatch] Starting server... KEEP THIS WINDOW OPEN.
echo [stopwatch] Open  http://127.0.0.1:8765  in your sapnilm.working@gmail.com Chrome profile.
echo [stopwatch] Press Ctrl+C in this window to stop the server.
echo.
".venv\Scripts\python.exe" server\main.py
if errorlevel 1 (
  echo.
  echo [stopwatch] The server exited with an error. The message above says why.
  echo.
  pause
)
exit /b 0

:err
echo.
echo [stopwatch] Setup failed. Install Python 3.10+ from https://python.org and re-run start.bat.
echo.
pause
exit /b 1
