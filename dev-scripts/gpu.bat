@echo off
cd /d "%~dp0\.."

echo Stopping old container...
docker stop auroracoder-agent-gpu >nul 2>&1
docker rm auroracoder-agent-gpu >nul 2>&1
timeout /t 2 /nobreak >nul

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
echo [ports] Scanning for available ports...
netstat -ano > "%TEMP%\_ac_ports.tmp" 2>nul
call :resolve_port BACKEND_PORT
echo [ports] BACKEND_PORT=%BACKEND_PORT%
call :resolve_port FRONTEND_PORT
echo [ports] FRONTEND_PORT=%FRONTEND_PORT%
call :resolve_port VNC_PORT
echo [ports] VNC_PORT=%VNC_PORT%
call :resolve_port TOOLSTORE_PORT
echo [ports] TOOLSTORE_PORT=%TOOLSTORE_PORT%
set /a "DEV_WIDTH=%DEV_PORT_END% - %DEV_PORT_START% + 1"
if %DEV_WIDTH% lss 1 set "DEV_WIDTH=3"
call :resolve_port_range DEV_PORT_START %DEV_WIDTH%
set /a "DEV_PORT_END=%DEV_PORT_START%+%DEV_WIDTH%-1"
echo [ports] DEV range=%DEV_PORT_START%-%DEV_PORT_END%
del "%TEMP%\_ac_ports.tmp" 2>nul

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
    echo [nv] Pulling NVIDIA vLLM image - one time only, ~9 GB...
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

echo.
echo ========================================
echo   AuroraCoder GPU is running
echo   http://localhost:%FRONTEND_PORT%
echo ========================================
echo.
echo Verify GPU: docker exec auroracoder-agent-gpu python -c "import torch; print(torch.cuda.get_device_name(0))"
echo.
echo Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start "" "http://localhost:%FRONTEND_PORT%"

goto :eof

:: ── Port utility subroutines ───────────────────────────────────────────

:resolve_port
setlocal enabledelayedexpansion
set "TRY=!%~1!"
set /a "MAX=!TRY!+1000"
:resolve_port_loop
findstr /c:":!TRY! " "%TEMP%\_ac_ports.tmp" >nul 2>&1
if errorlevel 1 (
    for %%v in ("!TRY!") do (
        endlocal
        set "%~1=%%~v"
    )
    exit /b
)
set /a "TRY+=1"
if !TRY! lss !MAX! goto :resolve_port_loop
for %%v in ("!TRY!") do (
    endlocal
    set "%~1=%%~v"
)
exit /b

:resolve_port_range
setlocal enabledelayedexpansion
set "BASE=!%~1!"
set "COUNT=%~2"
set /a "SAVE=!BASE!"
set /a "MAX=!BASE!+10000"
:resolve_range_loop
set /a "END=!BASE!+!COUNT!-1"
set "ALL_FREE=1"
for /l %%p in (!BASE!,1,!END!) do (
    findstr /c:":%%p " "%TEMP%\_ac_ports.tmp" >nul 2>&1
    if not errorlevel 1 set "ALL_FREE=0"
)
if "!ALL_FREE!"=="1" (
    for %%v in ("!BASE!") do (
        endlocal
        set "%~1=%%~v"
    )
    exit /b
)
set /a "BASE+=1"
if !BASE! lss !MAX! goto :resolve_range_loop
for %%v in ("!SAVE!") do (
    endlocal
    set "%~1=%%~v"
)
exit /b
