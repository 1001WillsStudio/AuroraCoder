#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AuroraCoder — Multi-instance launcher for Linux & macOS
# Equivalent to another-one.bat for Windows.
#
# Usage:
#   chmod +x another-one.sh
#   ./another-one.sh        # auto-picks next free instance number
#   ./another-one.sh 5      # explicit instance number
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# ── Instance configuration ──────────────────────────────────────────────
# Auto-detects the next free instance number (2, 3, 4, …).
# Or pass one explicitly:  ./another-one.sh 5
INST="${1:-}"
if [ -n "$INST" ]; then
    :  # explicit instance number provided
else
    INST=2
    while docker inspect --format="." "auroracoder-agent-$INST" >/dev/null 2>&1; do
        INST=$((INST + 1))
    done
fi

CONTAINER="auroracoder-agent-$INST"

# ── Stop old container FIRST ───────────────────────────────────────────
# By stopping the old container at the very beginning, ports have plenty
# of time to be released while we do all the other work below.
# This avoids a wasteful "sleep 2" right before the docker run.
echo "Stopping old container \"$CONTAINER\" if any..."
docker stop "$CONTAINER" >/dev/null 2>&1 || true
docker rm   "$CONTAINER" >/dev/null 2>&1 || true


# ── Base ports (from ports.conf or defaults) ────────────────────────────
BASE_FRONTEND=3000
BASE_BACKEND=8080
BASE_VNC=6080
BASE_TOOLSTORE=8765
BASE_DEV_START=8900

# Read ports.conf if it exists
if [ -f "ports.conf" ]; then
    while IFS='=' read -r key val; do
        case "$key" in
            FRONTEND_PORT) BASE_FRONTEND="$val" ;;
            BACKEND_PORT) BASE_BACKEND="$val" ;;
            VNC_PORT) BASE_VNC="$val" ;;
            TOOLSTORE_PORT) BASE_TOOLSTORE="$val" ;;
            DEV_PORT_START) BASE_DEV_START="$val" ;;
        esac
    done < <(grep -v '^#' ports.conf 2>/dev/null | grep -v '^$')
fi

# Port arithmetic — each instance offsets from the base by (INST-1)*2
OFFSET=$(( (INST - 1) * 2 ))
BACKEND_PORT=$((BASE_BACKEND + OFFSET))
VNC_PORT=$((BASE_VNC + OFFSET))
DEV_PORT_START=$((BASE_DEV_START + OFFSET * 3 / 2))
DEV_PORT_END=$((DEV_PORT_START + 2))
FRONTEND_PORT=$((BASE_FRONTEND + INST - 1))
TOOLSTORE_PORT=$((BASE_TOOLSTORE + INST - 1))

# ── Port availability helpers ───────────────────────────────────────────
port_is_free() {
    local port=$1
    if command -v ss >/dev/null 2>&1; then
        ! ss -tln "sport = :$port" 2>/dev/null | grep -q ":$port"
    elif command -v lsof >/dev/null 2>&1; then
        ! lsof -i ":$port" -sTCP:LISTEN >/dev/null 2>&1
    elif command -v netstat >/dev/null 2>&1; then
        ! netstat -tln 2>/dev/null | grep -q ":$port "
    else
        python3 -c "import socket; s=socket.socket(); s.bind(('',$port)); s.close()" 2>/dev/null
    fi
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
        for p in $(seq "$base" $((base + count - 1))); do
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
# Because we stopped the old container at the very beginning, these ports
# should already be free (or they'll auto-bump if occupied by something else).
BACKEND_PORT=$(find_free_port "$BACKEND_PORT")
FRONTEND_PORT=$(find_free_port "$FRONTEND_PORT")
VNC_PORT=$(find_free_port "$VNC_PORT")
TOOLSTORE_PORT=$(find_free_port "$TOOLSTORE_PORT")
DEV_WIDTH=$((DEV_PORT_END - DEV_PORT_START + 1))
[ "$DEV_WIDTH" -lt 1 ] && DEV_WIDTH=3
DEV_PORT_START=$(find_free_port_range "$DEV_PORT_START" "$DEV_WIDTH")
DEV_PORT_END=$((DEV_PORT_START + DEV_WIDTH - 1))

# ── Storage base ────────────────────────────────────────────────────────
if [ -d "$HOME/Documents" ]; then
    STORAGE_BASE="$HOME/Documents/AuroraCoder"
elif [ -d "$HOME/documents" ]; then
    STORAGE_BASE="$HOME/documents/AuroraCoder"
else
    STORAGE_BASE="$HOME/AuroraCoder"
fi
DATA_DIR="$STORAGE_BASE/data-$INST"
WORKSPACE_DIR="$STORAGE_BASE/workspace-$INST"

echo "========================================"
echo "  AuroraCoder  [Instance $INST]"
echo "========================================"
echo "  Frontend:       http://localhost:$FRONTEND_PORT"
echo "  Backend API:    http://localhost:$BACKEND_PORT"
echo "  API Docs:       http://localhost:$BACKEND_PORT/docs"
echo "  VNC Desktop:    http://localhost:$VNC_PORT"
echo "  ToolStore:      http://localhost:$TOOLSTORE_PORT"
echo "========================================"
echo ""

# ── Pre-flight checks ───────────────────────────────────────────────────
# The base + app images must already exist (built by start.sh)
if ! docker inspect --type=image auroracoder >/dev/null 2>&1; then
    echo "ERROR: App image \"auroracoder\" not found."
    echo "Run start.sh first to build the Docker images."
    exit 1
fi

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

# ── Data directories ────────────────────────────────────────────────────
mkdir -p "$DATA_DIR" "$WORKSPACE_DIR"

# ── Start backend container ─────────────────────────────────────────────
# No need for "sleep 2" here — the old container was stopped at the very
# beginning of the script, so ports have long been released.
echo "Starting backend in Docker (instance $INST)..."
docker run --rm -d \
    --name "$CONTAINER" \
    $ENV_FILE_ARG \
    -e AURORACODER_DOCKER=1 \
    -e AURORACODER_VNC=1 \
    -v "$DATA_DIR:/app/data" \
    -v "$WORKSPACE_DIR:/workspace" \
    -p "$BACKEND_PORT:8080" \
    -p "$VNC_PORT:6080" \
    -p "$DEV_PORT_START-$DEV_PORT_END:8900-8902" \
    -p "$FRONTEND_PORT:3000" \
    -p "$TOOLSTORE_PORT:8765" \
    auroracoder || {
    echo "Failed to start container."
    exit 1
}
echo "Container \"$CONTAINER\" started."
echo ""
echo "AuroraCoder instance $INST is running at http://localhost:$FRONTEND_PORT"
echo "To stop: docker stop $CONTAINER"
