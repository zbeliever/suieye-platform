@echo off
chcp 65001 >nul
rem ============================================================
rem  SuiEye dev launcher  --  backend :8300  +  frontend :5188
rem  用法：直接双击本文件。开两个窗口，关窗即停服务。
rem  ⚠️ 不要用 --reload：uvicorn 的 reload 子进程会成为孤儿占住端口，
rem     导致新代码不生效却 curl 200（"验收假失败"）。
rem ============================================================
setlocal
cd /d %~dp0

echo [1/3] 检查端口占用（5188 / 8300）...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r ":5188 .*LISTENING"') do (
    echo     5188 被 PID %%P 占用，正在清理...
    taskkill /F /T /PID %%P >nul 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r ":8300 .*LISTENING"') do (
    echo     8300 被 PID %%P 占用，正在清理...
    taskkill /F /T /PID %%P >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [2/3] 启动后端 :8300 ...
start "SuiEye Backend" cmd /k "cd /d %~dp0 && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8300"

echo [3/3] 启动前端 :5188 ...
start "SuiEye Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo 服务已启动，请等待 5~10 秒后访问： http://127.0.0.1:5188
echo （若前端窗口显示换到了 5189/5190，说明 5188 仍被占用，请关掉多余窗口重试）
echo.
pause
