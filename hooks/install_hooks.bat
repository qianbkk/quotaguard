@echo off
REM ============================================================
REM   QuotaGuard 一键启动器（Windows）
REM   - 启动 quota_guard monitor（后台）
REM   - 启动 quota_guard resume（前台，编排 claude）
REM   - 可选：启动 quota_guard proxy（供其他 Agent 用）
REM ============================================================

REM 1) UTF-8 + 项目根
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."

REM 2) Title
title QuotaGuard - MiniMax Quota Watcher

REM 3) 清代理
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=

cls
echo ============================================================
echo    QuotaGuard - MiniMax Quota Watcher
echo ============================================================
echo.

REM 4) 检查 .env
if not exist ".env" (
    echo [ERROR] .env not found. Create with: MINIMAX_API_KEY=sk-cp-...
    pause
    exit /b 1
)

REM 5) 安装 Python 依赖（如缺）
python -c "import requests, fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    python -m pip install --quiet requests fastapi uvicorn python-dotenv
    if errorlevel 1 (
        echo [ERROR] pip install failed
        pause
        exit /b 1
    )
)

REM 6) 创建 .quotaguard 目录
if not exist "%USERPROFILE%\.quotaguard" mkdir "%USERPROFILE%\.quotaguard"

REM 7) 选择启动模式
:loop
echo.
echo Choose mode:
echo   1) Full: monitor + resume (Claude Code)            [recommended]
echo   2) Full + Proxy: monitor + resume + proxy (multi-Agent)
echo   3) Monitor only (no auto-resume, just write PAUSE)
echo   4) Status (print current quota_state)
echo   5) Install Claude Code hooks to .claude/
echo   6) Exit
echo.
set /p MODE="Mode (1-6): "

if "%MODE%"=="1" goto :full
if "%MODE%"=="2" goto :full_with_proxy
if "%MODE%"=="3" goto :monitor_only
if "%MODE%"=="4" goto :status
if "%MODE%"=="5" goto :install_hooks
if "%MODE%"=="6" exit /b 0
goto :loop

:full
echo.
echo [INFO] Starting monitor (background) + resume (foreground, runs claude)
python -m quota_guard resume ^
    --monitor-cmd "python -m quota_guard monitor --low 15 --critical 5" ^
    --claude-cmd "claude --continue" ^
    --clean-on-start
goto :end

:full_with_proxy
echo.
echo [INFO] Starting proxy (background) + monitor + resume
start "QuotaGuard-Proxy" /B python -m quota_guard proxy --port 8080
timeout /t 2 /nobreak >nul
python -m quota_guard resume ^
    --monitor-cmd "python -m quota_guard monitor --low 15 --critical 5" ^
    --claude-cmd "claude --continue" ^
    --clean-on-start
goto :end

:monitor_only
echo.
echo [INFO] Starting monitor only (writes PAUSE.flag when critical)
python -m quota_guard monitor --low 15 --critical 5
goto :end

:status
echo.
python -m quota_guard status
echo.
pause
goto :loop

:install_hooks
echo.
echo [INFO] Installing Claude Code hooks to .claude/ ...
if not exist ".claude" mkdir .claude
if not exist ".claude\hooks" mkdir .claude\hooks
copy /Y "hooks\pretool_pause_check.py" ".claude\hooks\" >nul
copy /Y "hooks\sessionstart_inject.py" ".claude\hooks\" >nul
copy /Y "hooks\stop_log_progress.py" ".claude\hooks\" >nul
copy /Y "hooks\settings.json" ".claude\settings.json" >nul
echo [OK] Hooks installed. Restart Claude Code to activate.
pause
goto :loop

:end
echo.
echo ============================================================
echo    QuotaGuard stopped.
echo ============================================================
pause
