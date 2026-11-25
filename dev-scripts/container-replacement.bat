@echo off
if "%~1"=="prepare" goto :prepare
if "%~1"=="restore" goto :restore
if "%~1"=="finish" goto :finish
exit /b 1

:prepare
set "AURORA_SWAP_NAME=%~2"
set "AURORA_SWAP_OLD_ID="
set "AURORA_SWAP_WAS_RUNNING="
set "AURORA_SWAP_AUTO_REMOVE="
if not defined AURORA_DEV_LAUNCH_ID exit /b 1
set "AURORA_SWAP_CID_FILE=%TEMP%\aurora-launch-%AURORA_DEV_LAUNCH_ID%.cid"
if exist "%AURORA_SWAP_CID_FILE%" exit /b 1
for /f "tokens=1-3" %%a in ('docker container inspect --format "{{.Id}} {{.State.Running}} {{.HostConfig.AutoRemove}}" "%AURORA_SWAP_NAME%" 2^>nul') do (
    set "AURORA_SWAP_OLD_ID=%%a"
    set "AURORA_SWAP_WAS_RUNNING=%%b"
    set "AURORA_SWAP_AUTO_REMOVE=%%c"
)
if not defined AURORA_SWAP_OLD_ID exit /b 0
if "%AURORA_SWAP_AUTO_REMOVE%"=="true" goto :legacy
docker rename "%AURORA_SWAP_OLD_ID%" "%AURORA_SWAP_NAME%-previous-%AURORA_SWAP_OLD_ID:~0,12%"
if errorlevel 1 exit /b 1
if not "%AURORA_SWAP_WAS_RUNNING%"=="true" exit /b 0
docker stop "%AURORA_SWAP_OLD_ID%" >nul
if not errorlevel 1 goto :wait_for_port_release
docker rename "%AURORA_SWAP_OLD_ID%" "%AURORA_SWAP_NAME%"
exit /b 1

:legacy
echo Replacing a legacy auto-remove container. Its Docker state cannot be saved for rollback.
docker stop "%AURORA_SWAP_OLD_ID%" >nul
if errorlevel 1 exit /b 1
set "AURORA_SWAP_OLD_ID="

:wait_for_port_release
echo Waiting for Docker to release the previous ports...
powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 2"
if errorlevel 1 echo The port wait could not run. Availability will be checked before starting.
exit /b 0

:restore
set "AURORA_SWAP_FAILED_ID="
if exist "%AURORA_SWAP_CID_FILE%" (
    set /p AURORA_SWAP_FAILED_ID=<"%AURORA_SWAP_CID_FILE%"
    del "%AURORA_SWAP_CID_FILE%" >nul 2>&1
)
if not defined AURORA_SWAP_FAILED_ID goto :restore_previous
set "AURORA_SWAP_FAILED_RUNNING="
set "AURORA_SWAP_FAILED_NAME="
for /f "tokens=1,2" %%a in ('docker container inspect --format "{{.Name}} {{.State.Running}}" "%AURORA_SWAP_FAILED_ID%" 2^>nul') do (
    set "AURORA_SWAP_FAILED_NAME=%%a"
    set "AURORA_SWAP_FAILED_RUNNING=%%b"
)
if not defined AURORA_SWAP_FAILED_RUNNING goto :restore_previous
if not "%AURORA_SWAP_FAILED_NAME%"=="/%AURORA_SWAP_NAME%" (
    echo The failed container ID belongs to a different launch. Leaving it untouched.
    goto :restore_previous
)
if "%AURORA_SWAP_FAILED_RUNNING%"=="true" (
    docker stop "%AURORA_SWAP_FAILED_ID%" >nul
    if errorlevel 1 exit /b 1
)
docker rename "%AURORA_SWAP_FAILED_ID%" "%AURORA_SWAP_NAME%-failed-%AURORA_SWAP_FAILED_ID:~0,12%"
if errorlevel 1 exit /b 1
echo Failed replacement retained for inspection: docker logs %AURORA_SWAP_FAILED_ID%

:restore_previous
if not defined AURORA_SWAP_OLD_ID exit /b 0
docker rename "%AURORA_SWAP_OLD_ID%" "%AURORA_SWAP_NAME%"
if errorlevel 1 exit /b 1
if "%AURORA_SWAP_WAS_RUNNING%"=="true" (
    docker start "%AURORA_SWAP_OLD_ID%" >nul
    if errorlevel 1 exit /b 1
)
echo Restored the previous %AURORA_SWAP_NAME% container.
exit /b 0

:finish
if exist "%AURORA_SWAP_CID_FILE%" del "%AURORA_SWAP_CID_FILE%" >nul 2>&1
if defined AURORA_SWAP_OLD_ID (
    docker rm "%AURORA_SWAP_OLD_ID%" >nul
    if errorlevel 1 echo The previous stopped container remains available: %AURORA_SWAP_OLD_ID%
)
exit /b 0
