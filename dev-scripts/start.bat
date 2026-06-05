@echo off
cd /d "%~dp0\.."

:: ── Stop existing container FIRST ───────────────────────────────────────
echo [%time%] Stopping old container...
docker stop auroracoder-agent >nul 2>&1
echo [%time%] docker stop done
docker rm auroracoder-agent >nul 2>&1
echo [%time%] docker rm done

:: Short delay for port cleanup
echo [%time%] sleep 2...
timeout /t 2 /nobreak >nul
echo [%time%] sleep done


:: ── Port configuration ──────────────────────────────────────────────────
set "FRONTEND_PORT=3000"
set "BACKEND_PORT=8080"
set "VNC_PORT=6080"
set "TOOLSTORE_PORT=8765"
set "DEV_PORT_START=8900"
set "DEV_PORT_END=8902"

:: Read ports.conf if it exists
if exist "ports.conf" (
    for /f "usebackq tokens=1,2 delims==" %%a in (`findstr /v "^#" ports.conf 2^>nul`) do (
        if "%%a"=="FRONTEND_PORT" set "FRONTEND_PORT=%%b"
        if "%%a"=="BACKEND_PORT" set "BACKEND_PORT=%%b"
        if "%%a"=="VNC_PORT" set "VNC_PORT=%%b"
        if "%%a"=="TOOLSTORE_PORT" set "TOOLSTORE_PORT=%%b"
        if "%%a"=="DEV_PORT_START" set "DEV_PORT_START=%%b"
        if "%%a"=="DEV_PORT_END" set "DEV_PORT_END=%%b"
    )
)

:: ── Auto-find available ports ──────────────────────────────────────────
echo [%time%] netstat -ano (caching)...
netstat -ano > "%TEMP%\_ac_ports.tmp" 2>nul
echo [%time%] netstat done, resolving ports...
call :resolve_port BACKEND_PORT
echo [%time%] BACKEND_PORT=%BACKEND_PORT%
call :resolve_port FRONTEND_PORT
echo [%time%] FRONTEND_PORT=%FRONTEND_PORT%
call :resolve_port VNC_PORT
echo [%time%] VNC_PORT=%VNC_PORT%
call :resolve_port TOOLSTORE_PORT
echo [%time%] TOOLSTORE_PORT=%TOOLSTORE_PORT%
set /a "DEV_WIDTH=%DEV_PORT_END% - %DEV_PORT_START% + 1"
if %DEV_WIDTH% lss 1 set "DEV_WIDTH=3"
call :resolve_port_range DEV_PORT_START %DEV_WIDTH%
set /a "DEV_PORT_END=%DEV_PORT_START%+%DEV_WIDTH%-1"
echo [%time%] DEV range=%DEV_PORT_START%-%DEV_PORT_END%
del "%TEMP%\_ac_ports.tmp" 2>nul

echo ========================================
echo   AuroraCoder
echo ========================================
echo   Frontend:       http://localhost:%FRONTEND_PORT%
echo   Backend API:    http://localhost:%BACKEND_PORT%
echo   API Docs:       http://localhost:%BACKEND_PORT%/docs
echo   VNC Desktop:    http://localhost:%VNC_PORT%
echo   ToolStore:      http://localhost:%TOOLSTORE_PORT%
echo ========================================
echo.

:: Storage base — all persistent data lives under Documents\AuroraCoder
set "STORAGE_BASE=%USERPROFILE%\Documents\AuroraCoder"

:: ── Check if base image exists; build if missing ─────────────────────────
:: These Docker steps are slow — but because we stopped the old container
:: at the very beginning, ports have already been released by now.
docker inspect --type=image auroracoder-base >nul 2>&1
if errorlevel 1 goto :build_base
echo [base] Base image found, skipping.
goto :build_app

:build_base
echo [base] Building base image -- first time, this may take a few minutes...
docker build -t auroracoder-base -f docker\Dockerfile.base .
if errorlevel 1 (
    echo Base image build failed.
    pause
    exit /b 1
)
echo [base] Done.

:build_app
:: Always rebuild app image (fast: just copies source code)
:: Generate unique cache-bust key to force ToolStore reinstall every run
for /f "tokens=2 delims==." %%I in ('wmic os get localdatetime /value ^| find "="') do set "CACHEBUST=%%I"
echo [app] Building app image (cache-bust: %CACHEBUST%)...
docker build -t auroracoder --build-arg CACHEBUST=%CACHEBUST% -f docker\Dockerfile .
if errorlevel 1 (
    echo App image build failed.
    pause
    exit /b 1
)

:: ── Start backend container ─────────────────────────────────────────────
echo Starting backend in Docker (app + frontend)...
:: Check if .env exists; warn but don't abort (keys can be set via Settings UI)
set "ENV_FILE_ARG="
if exist ".env" (
    set "ENV_FILE_ARG=--env-file .env"
) else (
    echo NOTE: .env file not found. Starting without it.
    echo You can configure API keys via Settings UI at http://localhost:%FRONTEND_PORT%
    echo Or copy .env.example to .env and fill in your keys.
    echo.
)
if not exist "%STORAGE_BASE%\data" mkdir "%STORAGE_BASE%\data"
if not exist "%STORAGE_BASE%\workspace" mkdir "%STORAGE_BASE%\workspace"
docker run --rm -d --name auroracoder-agent %ENV_FILE_ARG% -e AURORACODER_DOCKER=1 -e AURORACODER_VNC=1 -v "%STORAGE_BASE%\data:/app/data" -v "%STORAGE_BASE%\workspace:/workspace" -p %BACKEND_PORT%:8080 -p %FRONTEND_PORT%:3000 -p %VNC_PORT%:6080 -p %TOOLSTORE_PORT%:8765 -p %DEV_PORT_START%-%DEV_PORT_END%:8900-8902 auroracoder
if errorlevel 1 (
    echo Failed to start container.
    pause
    exit /b 1
)
echo Container started.
echo.
echo AuroraCoder is running at http://localhost:%FRONTEND_PORT%
echo To stop: docker stop auroracoder-agent
echo.
echo Opening browser...
start "" "http://localhost:%FRONTEND_PORT%"

goto :eof

:: ── Port utility subroutines ───────────────────────────────────────────

:resolve_port
setlocal enabledelayedexpansion
set "TRY=!%~1!"
set /a "MAX=!TRY!+1000"
:resolve_port_loop
findstr /c:":!TRY! " "%TEMP%\_ac_ports.tmp" >nul 2>&1
if errorlevel 1 (
    for %%v in ("!TRY!") do (
        endlocal
        set "%~1=%%~v"
    )
    exit /b
)
set /a "TRY+=1"
if !TRY! lss !MAX! goto :resolve_port_loop
for %%v in ("!TRY!") do (
    endlocal
    set "%~1=%%~v"
)
exit /b

:resolve_port_range
setlocal enabledelayedexpansion
set "BASE=!%~1!"
set "COUNT=%~2"
set /a "SAVE=!BASE!"
set /a "MAX=!BASE!+10000"
:resolve_range_loop
set /a "END=!BASE!+!COUNT!-1"
set "ALL_FREE=1"
for /l %%p in (!BASE!,1,!END!) do (
    findstr /c:":%%p " "%TEMP%\_ac_ports.tmp" >nul 2>&1
    if not errorlevel 1 set "ALL_FREE=0"
)
if "!ALL_FREE!"=="1" (
    for %%v in ("!BASE!") do (
        endlocal
        set "%~1=%%~v"
    )
    exit /b
)
set /a "BASE+=1"
if !BASE! lss !MAX! goto :resolve_range_loop
for %%v in ("!SAVE!") do (
    endlocal
    set "%~1=%%~v"
)
exit /b
