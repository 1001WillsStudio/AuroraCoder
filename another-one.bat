@echo off
cd /d "%~dp0"

:: ── Instance configuration ──────────────────────────────────────────────
:: Auto-detects the next free instance number (2, 3, 4, …).
:: Or pass one explicitly:  another-one.bat 5
set "INST=%~1"
if not "%INST%"=="" goto :inst_ready

set "INST=2"
:find_next
docker inspect --format="." thinkwithtool-agent-%INST% >nul 2>&1
if errorlevel 1 goto :inst_ready
set /a "INST+=1"
goto :find_next

:inst_ready

:: Port arithmetic — each instance offsets from the base by (INST-1)*2
set /a "OFFSET=(%INST%-1)*2"
set /a "BACKEND_PORT=8080+%OFFSET%"
set /a "GATEWAY_PORT=8081+%OFFSET%"
set /a "VNC_PORT=6080+%OFFSET%"
set /a "DEV_PORT_START=8888+%OFFSET%*3/2"
set /a "DEV_PORT_END=%DEV_PORT_START%+2"
set /a "FRONTEND_PORT=3000+%INST%-1"

set "CONTAINER=thinkwithtool-agent-%INST%"
set "DATA_DIR=%cd%\data-%INST%"

echo ========================================
echo   AuroraCoder  [Instance %INST%]
echo ========================================
echo   Backend API:    http://localhost:%BACKEND_PORT%
echo   Convo History:  http://localhost:%GATEWAY_PORT%
echo   API Docs:       http://localhost:%BACKEND_PORT%/docs
echo   Frontend:       http://0.0.0.0:%FRONTEND_PORT%
echo   VNC Desktop:    http://localhost:%VNC_PORT%
echo ========================================
echo.

:: ── Pre-flight checks ───────────────────────────────────────────────────
:: The base + app images must already exist (built by start.bat)
docker inspect --type=image thinkwithtool >nul 2>&1
if errorlevel 1 (
    echo ERROR: App image "thinkwithtool" not found.
    echo Run start.bat first to build the Docker images.
    exit /b 1
)

if not exist ".env" (
    echo ERROR: .env file not found. Create it with your API keys.
    echo See .env.example for the required variables.
    exit /b 1
)

:: ── Build a filtered .env without your personal tokens ─────────────────
set "GUEST_ENV=%cd%\.env.guest-%INST%"
findstr /v /i "GITHUB_TOKEN" .env > "%GUEST_ENV%"

:: ── Data directory ──────────────────────────────────────────────────────
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

:: ── Stop old container if any ───────────────────────────────────────────
echo Stopping old container "%CONTAINER%" if any...
docker stop %CONTAINER% >nul 2>&1
docker rm   %CONTAINER% >nul 2>&1

:: ── Start backend container ─────────────────────────────────────────────
echo [1/2] Starting backend in Docker (instance %INST%)...
docker run --rm -d ^
    --name %CONTAINER% ^
    --env-file "%GUEST_ENV%" ^
    -e THINKTOOL_DOCKER=1 ^
    -e THINKTOOL_VNC=1 ^
    -v "%DATA_DIR%:/app/data" ^
    -p %BACKEND_PORT%:8080 ^
    -p %GATEWAY_PORT%:8081 ^
    -p %VNC_PORT%:6080 ^
    -p %DEV_PORT_START%-%DEV_PORT_END%:8888-8890 ^
    thinkwithtool
if errorlevel 1 (
    echo Failed to start container.
    exit /b 1
)
del "%GUEST_ENV%" >nul 2>&1
echo Container "%CONTAINER%" started (API :%BACKEND_PORT% + gateway :%GATEWAY_PORT%).
echo.

:: ── Frontend ────────────────────────────────────────────────────────────
:: Reuse the same node_modules from the primary frontend — no extra install needed.
:: Override the Vite dev-server port and proxy targets via environment variables.
echo [2/2] Starting frontend on http://0.0.0.0:%FRONTEND_PORT% ...
echo Press Ctrl+C to stop the frontend.
echo To stop the backend:  docker stop %CONTAINER%
echo.

cd frontend
set "VITE_PORT=%FRONTEND_PORT%"
set "VITE_BACKEND_PORT=%BACKEND_PORT%"
set "VITE_GATEWAY_PORT=%GATEWAY_PORT%"
npm run dev -- --host 0.0.0.0
