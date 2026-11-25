@echo off
setlocal EnableDelayedExpansion
set "PORT_CACHE=%TEMP%\auroracoder-ports-%RANDOM%-%RANDOM%.tmp"
netstat -ano > "%PORT_CACHE%" 2>nul
if errorlevel 1 goto :unavailable
set /a "DEV_WIDTH=DEV_PORT_END-DEV_PORT_START+1"
if !DEV_WIDTH! lss 1 set "DEV_WIDTH=3"

call :find_port BACKEND_PORT 1 1000 || goto :unavailable
call :find_port FRONTEND_PORT 1 1000 || goto :unavailable
call :find_port VNC_PORT 1 1000 || goto :unavailable
call :find_port TOOLSTORE_PORT 1 1000 || goto :unavailable
call :find_port DEV_PORT_START !DEV_WIDTH! 10000 || goto :unavailable
set /a "DEV_PORT_END=DEV_PORT_START+DEV_WIDTH-1"
del "%PORT_CACHE%" 2>nul
endlocal & set "BACKEND_PORT=%BACKEND_PORT%" & set "FRONTEND_PORT=%FRONTEND_PORT%" & set "VNC_PORT=%VNC_PORT%" & set "TOOLSTORE_PORT=%TOOLSTORE_PORT%" & set "DEV_PORT_START=%DEV_PORT_START%" & set "DEV_PORT_END=%DEV_PORT_END%"
exit /b 0

:find_port
set "CANDIDATE=!%~1!"
set /a "MAX=CANDIDATE+%~3"
:next_port
set /a "LAST=CANDIDATE+%~2-1"
if !CANDIDATE! geq !MAX! exit /b 1
if !LAST! gtr 65535 exit /b 1
set "BLOCKED="
for /l %%p in (!CANDIDATE!,1,!LAST!) do (
    findstr /c:":%%p " "%PORT_CACHE%" >nul 2>&1
    if not errorlevel 1 set "BLOCKED=1"
)
if defined BLOCKED (
    set /a "CANDIDATE+=1"
    goto :next_port
)
for /l %%p in (!CANDIDATE!,1,!LAST!) do >> "%PORT_CACHE%" echo :%%p reserved
set "%~1=!CANDIDATE!"
exit /b 0

:unavailable
del "%PORT_CACHE%" 2>nul
echo No free AuroraCoder ports found. Check ports.conf and current listeners. 1>&2
endlocal
exit /b 1
