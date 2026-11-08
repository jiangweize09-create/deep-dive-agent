@echo off
title Deep Dive Agent Server
echo ============================================
echo  Deep Dive Agent - restart server (port 8000)
echo ============================================
echo.
echo [1/2] Stopping old server on port 8000 ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo [2/2] Starting server with AUTO-RELOAD ...
echo      (from now on, code changes apply automatically - no more manual restart)
echo.
cd /d "%~dp0"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
echo.
echo Server stopped. Press any key to close.
pause >nul
