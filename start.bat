@echo off
cd /d "%~dp0"

echo ========================================
echo   AuroraCoder
echo ========================================
echo   Backend API:    http://localhost:8080
echo   Convo History:  http://localhost:8081
echo   API Docs:       http://localhost:8080/docs
echo   Frontend:       http://0.0.0.0:3000
echo   VNC Desktop:    http://localhost:6080
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

:: Storage base — all persistent data lives under Documents\ThinkTool
set "STORAGE_BASE=%USERPROFILE%\Documents\ThinkTool"

:: Start backend container (agent + conversation history server)
echo [1/2] Starting backend in Docker...
:: Verify .env file exists (contains API keys)
if not exist ".env" (
    echo ERROR: .env file not found. Create it with your API keys.
    echo See .env.example for the required variables.
    exit /b 1
)
if not exist "%STORAGE_BASE%\data" mkdir "%STORAGE_BASE%\data"
if not exist "%STORAGE_BASE%\workspace" mkdir "%STORAGE_BASE%\workspace"
docker run --rm -d --name thinkwithtool-agent --env-file .env -e THINKTOOL_DOCKER=1 -e THINKTOOL_VNC=1 -v "%STORAGE_BASE%\data:/app/data" -v "%STORAGE_BASE%\workspace:/workspace" -p 8080:8080 -p 8081:8081 -p 6080:6080 -p 8888-8890:8888-8890 thinkwithtool
if errorlevel 1 (
    echo Failed to start container.
    exit /b 1
)
echo Container started (agent API :8080 + conversation history :8081).

:: Install frontend dependencies only if package.json changed since last install
set "_need_install=0"
if not exist "frontend\node_modules" set "_need_install=1"
if not exist "frontend\node_modules\.package.json.cached" set "_need_install=1"
if "%_need_install%"=="0" (
    fc /b "frontend\package.json" "frontend\node_modules\.package.json.cached" >nul 2>&1
    if errorlevel 1 set "_need_install=1"
)
if "%_need_install%"=="1" (
    echo [2/2] package.json changed — running npm install...
    cd frontend && call npm install && cd ..
    if errorlevel 1 (
        echo Frontend dependency installation failed.
        exit /b 1
    )
    copy /y "frontend\package.json" "frontend\node_modules\.package.json.cached" >nul
) else (
    echo [2/2] package.json unchanged, skipping npm install.
)

:: Start frontend
echo Starting frontend on http://0.0.0.0:3000 ...
echo Press Ctrl+C to stop the frontend.
echo To stop the backend: docker stop thinkwithtool-agent
echo.
cd frontend
npm run dev -- --host 0.0.0.0
