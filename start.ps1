# Start both the FastAPI backend and React frontend for AuroraCoder
#
# Usage:
#   .\start.ps1                                  # Local mode (conda + npm)
#   .\start.ps1 -Docker                          # Docker mode, empty workspace
#   .\start.ps1 -Docker -Project C:\myapp        # Docker mode, seed from local dir
#   .\start.ps1 -Docker -VNC                     # Docker mode + VNC desktop (localhost:6080)
#   .\start.ps1 -Docker -VNC -Project C:\myapp   # Docker + VNC + seed

param(
    [switch]$Docker,
    [switch]$VNC,
    [string]$Project
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$CONTAINER_NAME = "thinkwithtool-agent"
$IMAGE_NAME = "thinkwithtool"

# ── Docker mode ─────────────────────────────────────────────────────────────

if ($Docker) {
    if ($VNC) {
        Write-Host "=== Docker Mode + VNC Desktop ===" -ForegroundColor Cyan
    } else {
        Write-Host "=== Docker Mode ===" -ForegroundColor Cyan
    }

    # Build image if needed
    $imageExists = docker images -q $IMAGE_NAME 2>$null
    if (-not $imageExists) {
        Write-Host "Building Docker image '$IMAGE_NAME' ..." -ForegroundColor Yellow
        docker build -t $IMAGE_NAME .
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Docker build failed." -ForegroundColor Red
            exit 1
        }
    }

    # Stop existing container if running
    $running = docker ps -q -f name=$CONTAINER_NAME 2>$null
    if ($running) {
        Write-Host "Stopping existing container..." -ForegroundColor Yellow
        docker stop $CONTAINER_NAME | Out-Null
        docker rm $CONTAINER_NAME | Out-Null
    }

    # Build docker run args
    $dockerArgs = @(
        "run", "--rm", "-d",
        "--name", $CONTAINER_NAME,
        "-p", "8080:8080",
        "-p", "8888-8890:8888-8890"
    )

    if ($VNC) {
        $dockerArgs += @("-p", "6080:6080", "-e", "THINKTOOL_VNC=1")
    }

    if ($Project) {
        $fullPath = (Resolve-Path $Project).Path
        Write-Host "Seeding workspace from: $fullPath" -ForegroundColor Cyan
        $dockerArgs += @("-v", "${fullPath}:/seed:ro")
    }

    $dockerArgs += $IMAGE_NAME

    # Start container
    docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to start container." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "Container '$CONTAINER_NAME' started." -ForegroundColor Green
    Write-Host "  Backend:  http://localhost:8080" -ForegroundColor White
    Write-Host "  API Docs: http://localhost:8080/docs" -ForegroundColor White
    Write-Host "  Dev ports: 8888-8890 (for agent dev servers)" -ForegroundColor White
    if ($VNC) {
        Write-Host "  Desktop:  http://localhost:6080  (VNC)" -ForegroundColor Magenta
    }
    Write-Host ""

    # Install frontend deps if needed & start frontend
    if (-not (Test-Path "frontend\node_modules")) {
        Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
        Push-Location frontend
        npm install
        Pop-Location
    }

    Write-Host "Starting frontend on http://localhost:3000 ..." -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop the frontend (container keeps running)." -ForegroundColor Yellow
    Write-Host "Run 'docker stop $CONTAINER_NAME' to stop the backend." -ForegroundColor Yellow
    Write-Host ""

    Push-Location frontend
    try {
        npm run dev
    } finally {
        Pop-Location
    }

    exit 0
}

# ── Local mode (original behaviour) ────────────────────────────────────────

$backendJob = $null
$frontendJob = $null

function Stop-All {
    Write-Host "`nShutting down..." -ForegroundColor Yellow
    if ($backendJob -and $backendJob.State -eq 'Running') {
        Stop-Job $backendJob -PassThru | Remove-Job -Force
    }
    if ($frontendJob -and $frontendJob.State -eq 'Running') {
        Stop-Job $frontendJob -PassThru | Remove-Job -Force
    }
    # Kill any leftover processes on the ports
    $procs = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique
    $procs | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

    $procs = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique
    $procs | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

    Write-Host "All processes stopped." -ForegroundColor Green
}

try {
    # Activate conda environment
    conda activate agent
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to activate conda env 'agent'. Make sure it exists." -ForegroundColor Red
        exit 1
    }

    # Install frontend dependencies if needed
    if (-not (Test-Path "frontend\node_modules")) {
        Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
        Push-Location frontend
        npm install
        Pop-Location
    }

    # Start backend as a background job
    Write-Host "Starting backend on http://localhost:8080 ..." -ForegroundColor Cyan
    $backendJob = Start-Job -ScriptBlock {
        Set-Location $using:ScriptDir
        conda activate agent
        python run_web.py
    }

    Start-Sleep -Seconds 3

    # Start frontend as a background job
    Write-Host "Starting frontend on http://localhost:3000 ..." -ForegroundColor Cyan
    $frontendJob = Start-Job -ScriptBlock {
        Set-Location "$using:ScriptDir\frontend"
        npm run dev
    }

    Write-Host ""
    Write-Host "=================================================" -ForegroundColor DarkCyan
    Write-Host "  Backend:  http://localhost:8080"                  -ForegroundColor White
    Write-Host "  API Docs: http://localhost:8080/docs"             -ForegroundColor White
    Write-Host "  Frontend: http://localhost:3000"                  -ForegroundColor White
    Write-Host "  Press Ctrl+C to stop both."                      -ForegroundColor Yellow
    Write-Host "=================================================" -ForegroundColor DarkCyan
    Write-Host ""

    # Stream output from both jobs until user presses Ctrl+C
    while ($true) {
        Receive-Job $backendJob -ErrorAction SilentlyContinue
        Receive-Job $frontendJob -ErrorAction SilentlyContinue

        if ($backendJob.State -ne 'Running' -and $frontendJob.State -ne 'Running') {
            Write-Host "Both processes have exited." -ForegroundColor Yellow
            Receive-Job $backendJob -ErrorAction SilentlyContinue
            Receive-Job $frontendJob -ErrorAction SilentlyContinue
            break
        }

        Start-Sleep -Milliseconds 500
    }
}
finally {
    Stop-All
}
