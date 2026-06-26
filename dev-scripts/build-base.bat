@echo off
cd /d "%~dp0\.."

echo === Build AuroraCoder Base Images ===

echo [base] Building CPU base...
docker build -t auroracoder-base -f docker\Dockerfile.base .
if errorlevel 1 (echo Build failed. & pause & exit /b 1)
echo [base] Done.

set "NV_IMAGE=nvcr.io/nvidia/vllm:26.05.post1-py3"
docker inspect --type=image "%NV_IMAGE%" >nul 2>&1
if errorlevel 1 (
    echo [nv] Pulling NVIDIA vLLM image (one time only, ~9 GB)...
    docker pull "%NV_IMAGE%"
    if errorlevel 1 (echo Pull failed. & pause & exit /b 1)
) else (
    echo [nv] NVIDIA vLLM image already cached.
)

echo [gpu-base] Building GPU base...
docker build -t auroracoder-gpu-base -f docker\Dockerfile.gpu-base .
if errorlevel 1 (echo Build failed. & pause & exit /b 1)
echo [gpu-base] Done.
echo All base images built.
pause
