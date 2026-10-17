@echo off
chcp 936 >nul 2>&1
title SuiEye Platform

echo.
echo   ============================================
echo     SuiEye Platform v0.1
echo   ============================================
echo.
echo   Path: %~dp0
echo   Backend: port 8300 (FastAPI)
echo   Frontend: port 5188 (Vite)
echo.

REM --- Auto-detect Python (managed > system) ---
set "PYTHON_EXE=python"
if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PYTHON_EXE=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
)

REM --- Auto-detect Node (managed > system) ---
set "NODE_EXE=node"
if exist "%USERPROFILE%\.workbuddy\binaries\node\versions\22.22.2-3\node.exe" (
    set "NODE_EXE=%USERPROFILE%\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
)

REM --- Backend ---
echo [1/2] Starting backend on :8300 ...
start "SuiEye-Backend" cmd /k "cd /d %~dp0 && "%PYTHON_EXE%" -m uvicorn backend.main:app --host 0.0.0.0 --port 8300 --reload"

REM --- Frontend ---
echo [2/2] Starting frontend on :5188 ...
start "SuiEye-Frontend" cmd /k "cd /d %~dp0frontend && "%NODE_EXE%" node_modules\vite\bin\vite.js --host 0.0.0.0 --port 5188"

REM --- Wait ---
echo.
echo [..] Waiting for services ...
timeout /t 6 /nobreak >nul

echo.
echo   ============================================
echo     DONE!
echo   ============================================
echo.
echo   LiveWall:  http://localhost:5188
echo   API Docs:  http://localhost:8300/docs
echo   Equipment: http://localhost:5188/equipment
echo   Dosing:     http://localhost:5188/dosing
echo.
echo   Press any key to exit (services keep running)...
pause >nul
