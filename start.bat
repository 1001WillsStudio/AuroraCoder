@echo off
cd /d "%~dp0"

echo ========================================
echo   AuroraCoder
echo ========================================
echo   Frontend:       http://localhost:3000
echo   Backend API:    http://localhost:8080
echo   API Docs:       http://localhost:8080/docs
echo   VNC Desktop:    http://localhost:6080
echo   ToolStore:      http://localhost:8765
echo ========================================
echo.

:: Read GITHUB_TOKEN from .env for ToolStore (used in base image build)
set "GITHUB_TOKEN="
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"GITHUB_TOKEN=" .env 2^>nul') do set "GITHUB_TOKEN=%%a"

:: Check if base image exists; build if missing
docker inspect --type=image thinkwithtool-base >nul 2>&1
if errorlevel 1 goto :build_base
echo [base] Base image found, skipping.
goto :build_app

:build_base
echo [base] Building base image -- first time, this may take a few minutes...
docker build -t thinkwithtool-base -f Dockerfile.base --build-arg GITHUB_TOKEN=%GITHUB_TOKEN% .
if errorlevel 1 (
    echo Base image build failed.
    pause
    exit /b 1
)
echo [base] Done.

:build_app

:: Always rebuild app image (fast: just copies source code)
echo [app] Building app image...
docker build -t thinkwithtool --build-arg GITHUB_TOKEN=%GITHUB_TOKEN% .
if errorlevel 1 (
    echo App image build failed.
    pause
    exit /b 1
)

:: Stop existing container if running
echo Stopping old container if any...
docker stop thinkwithtool-agent >nul 2>&1
docker rm thinkwithtool-agent >nul 2>&1

:: Storage base — all persistent data lives under Documents\ThinkTool
set "STORAGE_BASE=%USERPROFILE%\Documents\ThinkTool"

:: Start backend container (agent + conversation history server)
echo Starting backend in Docker (app + frontend)...
:: Verify .env file exists (contains API keys)
if not exist ".env" (
    echo ERROR: .env file not found. Create it with your API keys.
    echo See .env.example for the required variables.
    exit /b 1
)
if not exist "%STORAGE_BASE%\data" mkdir "%STORAGE_BASE%\data"
if not exist "%STORAGE_BASE%\workspace" mkdir "%STORAGE_BASE%\workspace"
docker run --rm -d --name thinkwithtool-agent --env-file .env -e THINKTOOL_DOCKER=1 -e THINKTOOL_VNC=1 -v "%STORAGE_BASE%\data:/app/data" -v "%STORAGE_BASE%\workspace:/workspace" -p 8080:8080 -p 3000:3000 -p 6080:6080 -p 8765:8765 -p 8900-8902:8900-8902 thinkwithtool
if errorlevel 1 (
    echo Failed to start container.
    pause
    exit /b 1
)
echo Container started.
echo.
echo AuroraCoder is running at http://localhost:3000
echo To stop: docker stop thinkwithtool-agent
