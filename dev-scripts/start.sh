#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AuroraCoder — One-click launcher for Linux & macOS
# Equivalent to start.bat for Windows.
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
# ──────────────────────────────────────────────────────────────────────────────
# ─── Helper: auto-open browser ───────────────────────────────────────────
open_browser() {
    local url="$1"
    case "$(uname -s)" in
        Darwin) open "$url" ;;
        Linux)
            if command -v xdg-open >/dev/null 2>&1; then
                xdg-open "$url"
            elif command -v sensible-browser >/dev/null 2>&1; then
                sensible-browser "$url"
            else
                echo "Please open $url in your browser."
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*) start "" "$url" ;;
        *) echo "Please open $url in your browser." ;;
    esac 2>/dev/null
}

# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# ── Stop existing container FIRST ─────────────────────────────────────────
# Docker builds take time — by stopping the old container at the very
# beginning, ports have the entire build duration to be released.
# This avoids a wasteful "sleep 2" blocking the final launch.
echo "Stopping old container if any..."
docker stop auroracoder-agent >/dev/null 2>&1 || true
docker rm auroracoder-agent >/dev/null 2>&1 || true

# Short delay to ensure ports are released before we start resolving them
echo "Waiting for port cleanup..."
sleep 2


# ── Port configuration ──────────────────────────────────────────────────
FRONTEND_PORT=3000
BACKEND_PORT=8080
VNC_PORT=6080
TOOLSTORE_PORT=8765
DEV_PORT_START=8900
DEV_PORT_END=8902

# Read ports.conf if it exists
if [ -f "ports.conf" ]; then
    while IFS='=' read -r key val; do
        case "$key" in
            FRONTEND_PORT) FRONTEND_PORT="$val" ;;
            BACKEND_PORT) BACKEND_PORT="$val" ;;
            VNC_PORT) VNC_PORT="$val" ;;
            TOOLSTORE_PORT) TOOLSTORE_PORT="$val" ;;
            DEV_PORT_START) DEV_PORT_START="$val" ;;
            DEV_PORT_END) DEV_PORT_END="$val" ;;
        esac
    done < <(grep -v '^#' ports.conf 2>/dev/null | grep -v '^$')
fi

# ── Port availability helpers ───────────────────────────────────────────
port_is_free() {
    local port=$1
    # bash-native TCP connect — zero subprocess overhead
    ! (: </dev/tcp/127.0.0.1/$port) 2>/dev/null
}

find_free_port() {
    local start=$1
    local port=$start
    while ! port_is_free "$port"; do
        port=$((port + 1))
        if [ "$port" -gt $((start + 1000)) ]; then
            echo "$start"
            return
        fi
    done
    echo "$port"
}

find_free_port_range() {
    local start=$1
    local count=$2
    local base=$start
    while true; do
        local all_free=true
        local p
        for (( p=base; p<=base+count-1; p++ )); do
            if ! port_is_free "$p"; then
                all_free=false
                break
            fi
        done
        if $all_free; then
            echo "$base"
            return
        fi
        base=$((base + 1))
        if [ "$base" -gt $((start + 10000)) ]; then
            echo "$start"
            return
        fi
    done
}

# ── Resolve ports: auto-find available ──────────────────────────────────
BACKEND_PORT=$(find_free_port "$BACKEND_PORT")
FRONTEND_PORT=$(find_free_port "$FRONTEND_PORT")
VNC_PORT=$(find_free_port "$VNC_PORT")
TOOLSTORE_PORT=$(find_free_port "$TOOLSTORE_PORT")
DEV_WIDTH=$((DEV_PORT_END - DEV_PORT_START + 1))
[ "$DEV_WIDTH" -lt 1 ] && DEV_WIDTH=3
DEV_PORT_START=$(find_free_port_range "$DEV_PORT_START" "$DEV_WIDTH")
DEV_PORT_END=$((DEV_PORT_START + DEV_WIDTH - 1))

echo "========================================"
echo "  AuroraCoder"
echo "========================================"
echo "  Frontend:       http://localhost:$FRONTEND_PORT"
echo "  Backend API:    http://localhost:$BACKEND_PORT"
echo "  API Docs:       http://localhost:$BACKEND_PORT/docs"
echo "  VNC Desktop:    http://localhost:$VNC_PORT"
echo "  ToolStore:      http://localhost:$TOOLSTORE_PORT"
echo "========================================"
echo ""

# ── Storage base — all persistent data lives under Documents/AuroraCoder ────
# Uses platform-appropriate Documents path
if [ -d "$HOME/Documents" ]; then
    STORAGE_BASE="$HOME/Documents/AuroraCoder"
elif [ -d "$HOME/documents" ]; then
    # Some Linux distros use lowercase
    STORAGE_BASE="$HOME/documents/AuroraCoder"
else
    # Fallback: just use home directory
    STORAGE_BASE="$HOME/AuroraCoder"
fi

# ── Check if base image exists; build if missing ─────────────────────────
# These Docker steps are slow — but because we stopped the old container
# at the very beginning, ports have already been released by now.
if docker inspect --type=image auroracoder-base >/dev/null 2>&1; then
    echo "[base] Base image found, skipping."
else
    echo "[base] Building base image -- first time, this may take a few minutes..."
    # Pre-pull base image -- tries Chinese mirrors if Docker Hub is unreachable
    BASE_IMAGE="python:3.12-slim-bookworm"
    if ! docker pull "$BASE_IMAGE" >/dev/null 2>&1; then
        echo "[mirror] Docker Hub unreachable, trying Chinese mirrors..."
        if docker pull "docker.m.daocloud.io/library/$BASE_IMAGE"; then
            docker tag "docker.m.daocloud.io/library/$BASE_IMAGE" "$BASE_IMAGE"
            echo "[mirror] Pulled via daoCloud."
        elif docker pull "hub-mirror.c.163.com/library/$BASE_IMAGE"; then
            docker tag "hub-mirror.c.163.com/library/$BASE_IMAGE" "$BASE_IMAGE"
            echo "[mirror] Pulled via NetEase."
        else
            echo "[mirror] All mirrors exhausted, proceeding anyway..."
        fi
    fi
    docker build -t auroracoder-base -f docker/Dockerfile.base . || {
        echo "Base image build failed."
        exit 1
    }
    echo "[base] Done."
fi

# ── Always rebuild app image (fast: just copies source code) ─────────────
echo "[app] Building app image..."
docker build -t auroracoder -f docker/Dockerfile . || {
    echo "App image build failed."
    exit 1
}

# ── Check if .env exists; warn but don't abort (keys can be set via Settings UI)
if [ -f ".env" ]; then
    ENV_FILE_ARG="--env-file .env"
else
    ENV_FILE_ARG=""
    echo "NOTE: .env file not found. Starting without it."
    echo "You can configure API keys via Settings UI at http://localhost:$FRONTEND_PORT"
    echo "Or copy .env.example to .env and fill in your keys."
    echo ""
fi

# ── Start backend container ──────────────────────────────────────────────
echo "Starting backend in Docker (app + frontend)..."
mkdir -p "$STORAGE_BASE/data" "$STORAGE_BASE/workspace"
docker run --rm -d \
    --name auroracoder-agent \
    $ENV_FILE_ARG \
    -e AURORACODER_DOCKER=1 \
    -e AURORACODER_VNC=1 \
    -v "$STORAGE_BASE/data:/app/data" \
    -v "$STORAGE_BASE/workspace:/workspace" \
    -p $BACKEND_PORT:8080 \
    -p $FRONTEND_PORT:3000 \
    -p $VNC_PORT:6080 \
    -p $TOOLSTORE_PORT:8765 \
    -p $DEV_PORT_START-$DEV_PORT_END:8900-8902 \
    auroracoder || {
    echo "Failed to start container."
    exit 1
}
echo "Container started."
echo ""
echo "AuroraCoder is running at http://localhost:$FRONTEND_PORT"
echo "To stop: docker stop auroracoder-agent"
echo ""
echo "Opening browser..."
(sleep 3 && open_browser "http://localhost:$FRONTEND_PORT") &
