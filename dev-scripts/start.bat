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
call "%~dp0port-availability.bat"
if errorlevel 1 exit /b 1
echo [ports] BACKEND_PORT=%BACKEND_PORT% FRONTEND_PORT=%FRONTEND_PORT% VNC_PORT=%VNC_PORT% TOOLSTORE_PORT=%TOOLSTORE_PORT%
echo [ports] DEV range=%DEV_PORT_START%-%DEV_PORT_END%

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
docker run --rm auroracoder-base test -f /tmp/.auroracoder-base-sentinel >nul 2>&1
if errorlevel 1 (
    echo [base] Stale cached image detected, rebuilding...
    docker rmi auroracoder-base >nul 2>&1
    goto :build_base
)
echo [base] Base image found, skipping.
goto :build_app

:build_base
echo [base] Building base image -- first time, this may take a few minutes...
:: Pre-pull base image -- tries Chinese mirrors if Docker Hub is unreachable
set "BASE_IMAGE=python:3.12-slim-bookworm"
docker pull %BASE_IMAGE% >nul 2>&1
if not errorlevel 1 goto :base_build_now
echo [mirror] Docker Hub unreachable, trying Chinese mirrors...
docker pull docker.m.daocloud.io/library/%BASE_IMAGE%
if errorlevel 1 goto :cnmirror2
docker tag docker.m.daocloud.io/library/%BASE_IMAGE% %BASE_IMAGE%
echo [mirror] Pulled via daoCloud.
goto :base_build_now
:cnmirror2
docker pull hub-mirror.c.163.com/library/%BASE_IMAGE%
if errorlevel 1 goto :cnmirror_fail
docker tag hub-mirror.c.163.com/library/%BASE_IMAGE% %BASE_IMAGE%
echo [mirror] Pulled via NetEase.
goto :base_build_now
:cnmirror_fail
echo [mirror] All mirrors exhausted, proceeding anyway...
goto :base_build_now
:base_build_now
docker build -t auroracoder-base -f docker\Dockerfile.base .
if errorlevel 1 (
    echo Base image build failed.
    pause
    exit /b 1
)
echo [base] Done.

:build_app
:: Rebuild app image — large modules (npm, tool-store) are Docker-layer cached.
:: Only source code layers rebuild, so this is fast after the first build.
echo [app] Building app image...
docker build -t auroracoder -f docker\Dockerfile .
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
echo Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start "" "http://localhost:%FRONTEND_PORT%"

goto :eof
