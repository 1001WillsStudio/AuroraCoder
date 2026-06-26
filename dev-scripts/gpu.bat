@echo off
cd /d "%~dp0\.."

echo Stopping old container...
docker stop auroracoder-agent-gpu >nul 2>&1
docker rm auroracoder-agent-gpu >nul 2>&1
timeout /t 2 /nobreak >nul

:: Ports
set FRONTEND_PORT=3000
set BACKEND_PORT=8080
set VNC_PORT=6080
set TOOLSTORE_PORT=8765
set DEV_PORT_START=8900
set DEV_PORT_END=8902

echo ========================================
echo   AuroraCoder GPU  (NVIDIA vLLM)
echo ========================================
echo   Frontend:     http://localhost:%FRONTEND_PORT%
echo   Backend API:  http://localhost:%BACKEND_PORT%
echo   VNC Desktop:  http://localhost:%VNC_PORT%
echo   ToolStore:    http://localhost:%TOOLSTORE_PORT%
echo ========================================
echo.

set "STORAGE_BASE=%USERPROFILE%\Documents\AuroraCoder-GPU"

:: ── Pre-pull NVIDIA vLLM image (~9 GB, one time only) ──────────────────
set "NV_IMAGE=nvcr.io/nvidia/vllm:26.05.post1-py3"
docker inspect --type=image "%NV_IMAGE%" >nul 2>&1
if errorlevel 1 (
    echo [nv] Pulling NVIDIA vLLM image (one time only, ~9 GB)...
    docker pull "%NV_IMAGE%"
    if errorlevel 1 (echo Pull failed. & pause & exit /b 1)
) else (
    echo [nv] NVIDIA vLLM image already cached.
)
echo.

:: ── Build GPU base ─────────────────────────────────────────────────────
docker inspect --type=image auroracoder-gpu-base >nul 2>&1
if errorlevel 1 goto :build_gpu_base
echo [gpu-base] Found, skipping.
goto :build_gpu

:build_gpu_base
echo [gpu-base] Building...
docker build -t auroracoder-gpu-base -f docker\Dockerfile.gpu-base .
if errorlevel 1 (echo Build failed. & pause & exit /b 1)
echo [gpu-base] Done.

:build_gpu
echo [gpu] Building app image...
docker build -t auroracoder-gpu -f docker\Dockerfile.gpu .
if errorlevel 1 (echo Build failed. & pause & exit /b 1)

:: Start
set ENV_FILE_ARG=
if exist ".env" (set "ENV_FILE_ARG=--env-file .env") else (echo NOTE: .env not found.)

if not exist "%STORAGE_BASE%\data" mkdir "%STORAGE_BASE%\data"
if not exist "%STORAGE_BASE%\workspace" mkdir "%STORAGE_BASE%\workspace"

docker run --rm -d --name auroracoder-agent-gpu --gpus all %ENV_FILE_ARG% ^
    -e AURORACODER_DOCKER=1 -e AURORACODER_VNC=1 -e AURORACODER_GPU=1 ^
    -v "%STORAGE_BASE%\data:/app/data" -v "%STORAGE_BASE%\workspace:/workspace" ^
    -p %BACKEND_PORT%:8080 -p %FRONTEND_PORT%:3000 -p %VNC_PORT%:6080 ^
    -p %TOOLSTORE_PORT%:8765 -p %DEV_PORT_START%-%DEV_PORT_END%:8900-8902 ^
    auroracoder-gpu

if errorlevel 1 (echo Container failed. & pause & exit /b 1)

echo Running at http://localhost:%FRONTEND_PORT%
echo Verify: docker exec auroracoder-agent-gpu python -c "import torch; print(torch.cuda.get_device_name(0))"
echo.
timeout /t 3 /nobreak >nul
start "" "http://localhost:%FRONTEND_PORT%"
