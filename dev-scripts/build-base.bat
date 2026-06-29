@echo off
cd /d "%~dp0\.."

echo === Build AuroraCoder Base Images ===

echo [base] Building CPU base...
:: Pre-pull base image -- tries Chinese mirrors if Docker Hub is unreachable
set "BASE_IMAGE=python:3.12-slim-bookworm"
docker pull %BASE_IMAGE% >nul 2>&1
if not errorlevel 1 goto :bld_cpu
echo [mirror] Docker Hub unreachable, trying Chinese mirrors...
docker pull docker.m.daocloud.io/library/%BASE_IMAGE%
if errorlevel 1 goto :bld_cpu_m2
docker tag docker.m.daocloud.io/library/%BASE_IMAGE% %BASE_IMAGE%
echo [mirror] Pulled via daoCloud.
goto :bld_cpu
:bld_cpu_m2
docker pull hub-mirror.c.163.com/library/%BASE_IMAGE%
if errorlevel 1 goto :bld_cpu_fail
docker tag hub-mirror.c.163.com/library/%BASE_IMAGE% %BASE_IMAGE%
echo [mirror] Pulled via NetEase.
goto :bld_cpu
:bld_cpu_fail
echo [mirror] All mirrors exhausted, proceeding anyway...
:bld_cpu
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
