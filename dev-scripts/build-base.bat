@echo off
cd /d "%~dp0\.."

echo ========================================
echo   Build AuroraCoder Base Image
echo   (Node.js + Python env + ToolStore)
echo ========================================
echo.

:: Read GITHUB_TOKEN from .env for ToolStore
set "GITHUB_TOKEN="
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"GITHUB_TOKEN=" .env 2^>nul') do set "GITHUB_TOKEN=%%a"
if "%GITHUB_TOKEN%"=="" (
    echo WARNING: GITHUB_TOKEN not found in .env — ToolStore install may be skipped.
)

echo [base] Building base image...
docker build -t thinkwithtool-base -f docker\Dockerfile.base --build-arg GITHUB_TOKEN=%GITHUB_TOKEN% .
if errorlevel 1 (
    echo Base image build failed.
    pause
    exit /b 1
)
echo [base] Done.
pause
