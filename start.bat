@echo off
cd /d "%~dp0"

echo ========================================
echo   AuroraCoder
echo ========================================
echo   Backend API:  http://localhost:8080
echo   API Docs:     http://localhost:8080/docs
echo   Frontend:     http://localhost:3000
echo   VNC Desktop:  http://localhost:6080
echo ========================================
echo.

:: Check if base image exists; build if missing
docker inspect --type=image thinkwithtool-base >nul 2>&1
if errorlevel 1 goto :build_base
echo [base] Base image found, skipping.
goto :build_app

:build_base
echo [base] Building base image -- first time, this may take a few minutes...
docker build -t thinkwithtool-base -f Dockerfile.base .
if errorlevel 1 (
    echo Base image build failed.
    exit /b 1
)
echo [base] Done.

:build_app
:: Always rebuild app image (fast: just copies source code)
echo [app] Building app image...
docker build -t thinkwithtool .
if errorlevel 1 (
    echo App image build failed.
    exit /b 1
)

:: Stop existing container if running
echo Stopping old container if any...
docker stop thinkwithtool-agent >nul 2>&1
docker rm thinkwithtool-agent >nul 2>&1

:: Start backend container
echo [1/2] Starting backend in Docker...
docker run --rm -d --name thinkwithtool-agent -e THINKTOOL_DOCKER=1 -e THINKTOOL_VNC=1 -p 8080:8080 -p 6080:6080 -p 8888-8890:8888-8890 thinkwithtool
if errorlevel 1 (
    echo Failed to start container.
    exit /b 1
)
echo Container started.

:: Install frontend dependencies if needed
if not exist "frontend\node_modules" (
    echo [2/2] Installing frontend dependencies...
    cd frontend && call npm install && cd ..
) else (
    echo [2/2] Frontend dependencies already installed.
)

:: Start frontend
echo Starting frontend on http://localhost:3000 ...
echo Press Ctrl+C to stop the frontend.
echo To stop the backend: docker stop thinkwithtool-agent
echo.
cd frontend
npm run dev
