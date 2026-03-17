@echo off
cd /d "%~dp0"

echo ========================================
echo   AuroraCoder
echo ========================================
echo   Backend API:  http://localhost:8080
echo   API Docs:     http://localhost:8080/docs
echo   Frontend:     http://localhost:3000
echo ========================================
echo.

:: Build Docker image if needed
docker images -q thinkwithtool >nul 2>&1
if %errorlevel% neq 0 (
    echo Building Docker image...
    docker build -t thinkwithtool .
    if %errorlevel% neq 0 (
        echo Docker build failed.
        exit /b 1
    )
)

:: Stop existing container if running
docker ps -q -f name=thinkwithtool-agent >nul 2>&1 && (
    echo Stopping existing container...
    docker stop thinkwithtool-agent >nul 2>&1
    docker rm thinkwithtool-agent >nul 2>&1
)

:: Start backend container
echo [1/2] Starting backend in Docker...
docker run --rm -d --name thinkwithtool-agent -p 8080:8080 -p 8888-8890:8888-8890 thinkwithtool
if %errorlevel% neq 0 (
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
echo Press Ctrl+C to stop the frontend (run "docker stop thinkwithtool-agent" to stop the backend).
echo.
cd frontend
npm run dev
