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

:: ── Pre-flight checks ───────────────────────────────────────────────────
:: The base + app images must already exist (built by start.bat)
docker inspect --type=image auroracoder >nul 2>&1
if errorlevel 1 (
    echo ERROR: App image "auroracoder" not found.
    echo Run start.bat first to build the Docker images.
    exit /b 1
)

call "%~dp0container-replacement.bat" prepare "%CONTAINER%"
if errorlevel 1 exit /b 1

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
call "%~dp0port-availability.bat"
if errorlevel 1 goto :replacement_failed
echo [ports] BACKEND_PORT=%BACKEND_PORT% FRONTEND_PORT=%FRONTEND_PORT% VNC_PORT=%VNC_PORT% TOOLSTORE_PORT=%TOOLSTORE_PORT%
echo [ports] DEV range=%DEV_PORT_START%-%DEV_PORT_END%

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
echo Starting backend in Docker (instance %INST%)...
docker run -d --cidfile "%AURORA_SWAP_CID_FILE%" ^
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
if errorlevel 1 goto :replacement_failed
set "AURORA_SWAP_NEW_ID="
set /p AURORA_SWAP_NEW_ID=<"%AURORA_SWAP_CID_FILE%"
if not defined AURORA_SWAP_NEW_ID goto :replacement_failed

echo Waiting for AuroraCoder services...
docker exec "%AURORA_SWAP_NEW_ID%" python3 /app/src/readiness.py
if errorlevel 1 goto :replacement_failed
call "%~dp0container-replacement.bat" finish
echo.
echo AuroraCoder instance %INST% is running at http://localhost:%FRONTEND_PORT%
echo To stop: docker stop %CONTAINER%
echo.
echo Opening browser...
start "" "http://localhost:%FRONTEND_PORT%"
pause
goto :eof

:replacement_failed
echo The replacement did not start successfully.
call "%~dp0container-replacement.bat" restore
if errorlevel 1 echo Automatic Docker rollback failed.
exit /b 1
