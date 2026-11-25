@echo off
cd /d "%~dp0\.."
if defined AURORA_DEV_LAUNCH_ID goto :launch
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0windows-launch-lock.ps1" -BatchPath "%~f0"
exit /b %errorlevel%

:launch

:: ── Pre-pull NVIDIA vLLM image (~9 GB, one time only) ──────────────────
set "NV_IMAGE=nvcr.io/nvidia/vllm:26.05.post1-py3"
docker inspect --type=image "%NV_IMAGE%" >nul 2>&1
if errorlevel 1 (
    echo [nv] Pulling NVIDIA vLLM image - one time only, ~9 GB...
    docker pull "%NV_IMAGE%"
    if errorlevel 1 (echo Pull failed. & exit /b 1)
) else (
    echo [nv] NVIDIA vLLM image already cached.
)
echo.

:: ── Build GPU base ─────────────────────────────────────────────────────
docker inspect --type=image auroracoder-gpu-base >nul 2>&1
if errorlevel 1 goto :build_gpu_base
docker run --rm auroracoder-gpu-base test -f /tmp/.auroracoder-gpu-base-sentinel >nul 2>&1
if errorlevel 1 (
    echo [gpu-base] Stale cached image detected, rebuilding...
    docker rmi auroracoder-gpu-base >nul 2>&1
    goto :build_gpu_base
)
echo [gpu-base] Found, skipping.
goto :build_gpu

:build_gpu_base
echo [gpu-base] Building...
docker build -t auroracoder-gpu-base -f docker\Dockerfile.gpu-base .
if errorlevel 1 (echo Build failed. & exit /b 1)
echo [gpu-base] Done.

:build_gpu
echo [gpu] Building app image...
docker build -t auroracoder-gpu -f docker\Dockerfile.gpu .
if errorlevel 1 (echo Build failed. & exit /b 1)

call "%~dp0container-replacement.bat" prepare "auroracoder-agent-gpu"
if errorlevel 1 exit /b 1

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
if errorlevel 1 goto :replacement_failed
echo [ports] BACKEND_PORT=%BACKEND_PORT% FRONTEND_PORT=%FRONTEND_PORT% VNC_PORT=%VNC_PORT% TOOLSTORE_PORT=%TOOLSTORE_PORT%
echo [ports] DEV range=%DEV_PORT_START%-%DEV_PORT_END%

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

:: Start
set ENV_FILE_ARG=
if exist ".env" (set "ENV_FILE_ARG=--env-file .env") else (echo NOTE: .env not found.)

if not exist "%STORAGE_BASE%\data" mkdir "%STORAGE_BASE%\data"
if not exist "%STORAGE_BASE%\workspace" mkdir "%STORAGE_BASE%\workspace"

docker run -d --cidfile "%AURORA_SWAP_CID_FILE%" --name auroracoder-agent-gpu --gpus all %ENV_FILE_ARG% ^
    -e AURORACODER_DOCKER=1 -e AURORACODER_VNC=1 -e AURORACODER_GPU=1 ^
    -v "%STORAGE_BASE%\data:/app/data" -v "%STORAGE_BASE%\workspace:/workspace" ^
    -v //var/run/docker.sock:/var/run/docker.sock ^
    -p %BACKEND_PORT%:8080 -p %FRONTEND_PORT%:3000 -p %VNC_PORT%:6080 ^
    -p %TOOLSTORE_PORT%:8765 -p %DEV_PORT_START%-%DEV_PORT_END%:8900-8902 ^
    auroracoder-gpu

if errorlevel 1 goto :replacement_failed
set "AURORA_SWAP_NEW_ID="
set /p AURORA_SWAP_NEW_ID=<"%AURORA_SWAP_CID_FILE%"
if not defined AURORA_SWAP_NEW_ID goto :replacement_failed

echo Waiting for AuroraCoder services...
docker exec "%AURORA_SWAP_NEW_ID%" python3 /app/src/readiness.py
if errorlevel 1 goto :replacement_failed
call "%~dp0container-replacement.bat" finish

echo.
echo ========================================
echo   AuroraCoder GPU is running
echo   http://localhost:%FRONTEND_PORT%
echo ========================================
echo.
echo Verify GPU: docker exec auroracoder-agent-gpu python -c "import torch; print(torch.cuda.get_device_name(0))"
echo.
echo Opening browser...
start "" "http://localhost:%FRONTEND_PORT%"

goto :eof

:replacement_failed
echo The replacement did not start successfully.
call "%~dp0container-replacement.bat" restore
if errorlevel 1 echo Automatic Docker rollback failed.
exit /b 1
