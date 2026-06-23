@echo off
cd /d "%~dp0\.."

echo ========================================
echo   Build AuroraCoder Base Image
echo   (Node.js + Python env + ToolStore)
echo ========================================
echo.


echo [base] Building base image...
docker build -t auroracoder-base -f docker\Dockerfile.base .
if errorlevel 1 (
    echo Base image build failed.
    pause
    exit /b 1
)
echo [base] Done.

echo.
echo ========================================
echo   Building GPU Base Image (PyTorch + CUDA)
echo ========================================
echo.
docker build -t auroracoder-gpu-base -f docker\Dockerfile.gpu-base .
if errorlevel 1 (
    echo GPU base image build failed.
    pause
    exit /b 1
)
echo [gpu-base] Done.
echo.
echo All base images built successfully.
pause
