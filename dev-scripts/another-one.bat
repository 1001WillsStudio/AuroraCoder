@echo off
cd /d "%~dp0\.."

:: ── Instance configuration ──────────────────────────────────────────────
:: Auto-detects the next free instance number (2, 3, 4, …).
:: Or pass one explicitly:  another-one.bat 5
set "INST=%~1"
if not "%INST%"=="" goto :inst_ready

set "INST=2"
:find_next
docker inspect --format="." auroracoder-agent-%INST% >nul 2>&1
if errorlevel 1 goto :inst_ready
set /a "INST+=1"
goto :find_next

:inst_ready

set "CONTAINER=auroracoder-agent-%INST%"

:: ── Stop old container FIRST ───────────────────────────────────────────
echo [%time%] Stopping old container "%CONTAINER%"...
docker stop %CONTAINER% >nul 2>&1
echo [%time%] docker stop done
docker rm   %CONTAINER% >nul 2>&1
echo [%time%] docker rm done

echo [%time%] sleep 2...
timeout /t 2 /nobreak >nul
echo [%time%] sleep done

:: ── Base ports (from ports.conf or defaults) ────────────────────────────
set "BASE_FRONTEND=3000"
set "BASE_BACKEND=8080"
set "BASE_VNC=6080"
set "BASE_TOOLSTORE=8765"
set "BASE_DEV_START=8900"

:: Read ports.conf if it exists
if exist "ports.conf" (
    for /f "usebackq tokens=1,2 delims==" %%a in (`findstr /v "^#" ports.conf 2^>nul`) do (
        if "%%a"=="FRONTEND_PORT" set "BASE_FRONTEND=%%b"
        if "%%a"=="BACKEND_PORT" set "BASE_BACKEND=%%b"
        if "%%a"=="VNC_PORT" set "BASE_VNC=%%b"
        if "%%a"=="TOOLSTORE_PORT" set "BASE_TOOLSTORE=%%b"
        if "%%a"=="DEV_PORT_START" set "BASE_DEV_START=%%b"
    )
)

:: Port arithmetic — each instance offsets from the base by (INST-1)*2
set /a "OFFSET=(%INST%-1)*2"
set /a "BACKEND_PORT=%BASE_BACKEND%+%OFFSET%"
set /a "VNC_PORT=%BASE_VNC%+%OFFSET%"
set /a "DEV_PORT_START=%BASE_DEV_START%+%OFFSET%*3/2"
set /a "DEV_PORT_END=%DEV_PORT_START%+2"
set /a "FRONTEND_PORT=%BASE_FRONTEND%+%INST%-1"
set /a "TOOLSTORE_PORT=%BASE_TOOLSTORE%+%INST%-1"

:: ── Auto-find available ports ──────────────────────────────────────────
echo [%time%] netstat -ano (caching)...
netstat -ano > "%TEMP%\_ac_ports.tmp" 2>nul
echo [%time%] netstat done, resolving...
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

set "STORAGE_BASE=%USERPROFILE%\Documents\AuroraCoder"
set "DATA_DIR=%STORAGE_BASE%\data-%INST%"
set "WORKSPACE_DIR=%STORAGE_BASE%\workspace-%INST%"

echo ========================================
echo   AuroraCoder  [Instance %INST%]
echo ========================================
echo   Frontend:       http://localhost:%FRONTEND_PORT%
echo   Backend API:    http://localhost:%BACKEND_PORT%
echo   API Docs:       http://localhost:%BACKEND_PORT%/docs
echo   VNC Desktop:    http://localhost:%VNC_PORT%
echo   ToolStore:      http://localhost:%TOOLSTORE_PORT%
echo ========================================
echo.

:: ── Pre-flight checks ───────────────────────────────────────────────────
:: The base + app images must already exist (built by start.bat)
docker inspect --type=image auroracoder >nul 2>&1
if errorlevel 1 (
    echo ERROR: App image "auroracoder" not found.
    echo Run start.bat first to build the Docker images.
    exit /b 1
)

:: Check if .env exists; warn but don't abort (keys can be set via Settings UI)
if exist ".env" (
    set "ENV_FILE_ARG=--env-file .env"
) else (
    set "ENV_FILE_ARG="
    echo NOTE: .env file not found. Starting without it.
    echo You can configure API keys via Settings UI at http://localhost:%FRONTEND_PORT%
    echo Or copy .env.example to .env and fill in your keys.
    echo.
)

:: ── Data directory ──────────────────────────────────────────────────────
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
if not exist "%WORKSPACE_DIR%" mkdir "%WORKSPACE_DIR%"

:: ── Start backend container ─────────────────────────────────────────────
:: No need for "timeout 2" here — the old container was stopped at the very
:: beginning of the script, so ports have long been released.
echo Starting backend in Docker (instance %INST%)...
docker run --rm -d ^
    --name %CONTAINER% ^
    %ENV_FILE_ARG% ^
    -e AURORACODER_DOCKER=1 ^
    -e AURORACODER_VNC=1 ^
    -v "%DATA_DIR%:/app/data" ^
    -v "%WORKSPACE_DIR%:/workspace" ^
    -p %BACKEND_PORT%:8080 ^
    -p %VNC_PORT%:6080 ^
    -p %DEV_PORT_START%-%DEV_PORT_END%:8900-8902 ^
    -p %FRONTEND_PORT%:3000 ^
    -p %TOOLSTORE_PORT%:8765 ^
    auroracoder
if errorlevel 1 (
    echo Failed to start container.
    exit /b 1
)
echo Container "%CONTAINER%" started.
echo.
echo AuroraCoder instance %INST% is running at http://localhost:%FRONTEND_PORT%
echo To stop: docker stop %CONTAINER%
echo.
echo Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start "" "http://localhost:%FRONTEND_PORT%"
pause
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
