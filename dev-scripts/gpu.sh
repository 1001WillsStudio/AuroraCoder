#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AuroraCoder GPU — Launcher with NVIDIA GPU passthrough
#
# Starts AuroraCoder with host GPU access via Docker's --gpus flag.
# Requires: NVIDIA drivers + nvidia-container-toolkit (Linux) or
#           Docker Desktop with WSL2 backend (Windows).
#
# Usage:
#   chmod +x gpu.sh
#   ./gpu.sh
#
#   # Pass CUDA version for PyTorch (default: cu128 for Blackwell sm_120)
#   AURORACODER_CUDA=cu130 ./gpu.sh
#
#   # Limit to specific GPU
#   AURORACODER_GPU_DEVICE=0 ./gpu.sh
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

# ── GPU configuration ─────────────────────────────────────────────────────
CUDA_INDEX="${AURORACODER_CUDA:-cu128}"
GPU_FLAG="--gpus"
GPU_DEVICE="${AURORACODER_GPU_DEVICE:-all}"
GPU_ARG="$GPU_FLAG $GPU_DEVICE"

echo "[gpu] CUDA index: $CUDA_INDEX"
echo "[gpu] GPU argument: $GPU_ARG"

# ── Stop existing container FIRST ─────────────────────────────────────────
echo "Stopping old container if any..."
docker stop auroracoder-agent-gpu >/dev/null 2>&1 || true
docker rm auroracoder-agent-gpu >/dev/null 2>&1 || true

# Short delay to ensure ports are released
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

# ── Resolve ports ────────────────────────────────────────────────────────
BACKEND_PORT=$(find_free_port "$BACKEND_PORT")
FRONTEND_PORT=$(find_free_port "$FRONTEND_PORT")
VNC_PORT=$(find_free_port "$VNC_PORT")
TOOLSTORE_PORT=$(find_free_port "$TOOLSTORE_PORT")
DEV_WIDTH=$((DEV_PORT_END - DEV_PORT_START + 1))
[ "$DEV_WIDTH" -lt 1 ] && DEV_WIDTH=3
DEV_PORT_START=$(find_free_port_range "$DEV_PORT_START" "$DEV_WIDTH")
DEV_PORT_END=$((DEV_PORT_START + DEV_WIDTH - 1))

echo "========================================"
echo "  AuroraCoder GPU"
echo "========================================"
echo "  Frontend:       http://localhost:$FRONTEND_PORT"
echo "  Backend API:    http://localhost:$BACKEND_PORT"
echo "  API Docs:       http://localhost:$BACKEND_PORT/docs"
echo "  VNC Desktop:    http://localhost:$VNC_PORT"
echo "  ToolStore:      http://localhost:$TOOLSTORE_PORT"
echo "========================================"
echo ""

# ── Storage base ────────────────────────────────────────────────────────
if [ -d "$HOME/Documents" ]; then
    STORAGE_BASE="$HOME/Documents/AuroraCoder-GPU"
elif [ -d "$HOME/documents" ]; then
    STORAGE_BASE="$HOME/documents/AuroraCoder-GPU"
else
    STORAGE_BASE="$HOME/AuroraCoder-GPU"
fi

# ── Build base image if missing ──────────────────────────────────────────
if docker inspect --type=image auroracoder-base >/dev/null 2>&1; then
    echo "[base] Base image found, skipping."
else
    echo "[base] Building base image -- first time, this may take a few minutes..."
    docker build -t auroracoder-base -f docker/Dockerfile.base . || {
        echo "Base image build failed."
        exit 1
    }
    echo "[base] Done."
fi

# ── Build GPU base image if missing (PyTorch + CUDA) ─────────────────────
# CUDA_INDEX can be overridden via AURORACODER_CUDA env var.
if docker inspect --type=image auroracoder-gpu-base >/dev/null 2>&1; then
    echo "[gpu-base] GPU base image found, skipping."
else
    echo "[gpu-base] Building GPU base image (PyTorch + CUDA: ${CUDA_INDEX}) -- this may take a few minutes..."
    docker build \
        -t auroracoder-gpu-base \
        --build-arg CUDA_INDEX="$CUDA_INDEX" \
        -f docker/Dockerfile.gpu-base . || {
        echo "GPU base image build failed."
        exit 1
    }
    echo "[gpu-base] Done."
fi

# ── Build GPU app image (fast: only source layers, PyTorch is in gpu-base) ──
echo "[gpu] Building GPU app image..."
docker build \
    -t auroracoder-gpu \
    -f docker/Dockerfile.gpu . || {
    echo "GPU app image build failed."
    exit 1
}

# ── Check if .env exists ─────────────────────────────────────────────────
if [ -f ".env" ]; then
    ENV_FILE_ARG="--env-file .env"
else
    ENV_FILE_ARG=""
    echo "NOTE: .env file not found. Starting without it."
    echo "You can configure API keys via Settings UI at http://localhost:$FRONTEND_PORT"
    echo "Or copy .env.example to .env and fill in your keys."
    echo ""
fi

# ── Pre-flight: warn if nvidia-smi not available ─────────────────────────
if ! command -v nvidia-smi >/dev/null 2>&1 && ! docker run --rm --gpus all nvidia/cuda:12.8-base nvidia-smi >/dev/null 2>&1; then
    echo "WARNING: nvidia-smi not found. GPU passthrough may not work."
    echo "  Linux: install nvidia-container-toolkit"
    echo "  Windows: ensure Docker Desktop uses WSL2 backend + NVIDIA drivers"
    echo "Continuing anyway (CPU fallback)..."
    echo ""
fi

# ── Start backend container ──────────────────────────────────────────────
echo "Starting backend in Docker (app + frontend + GPU)..."
mkdir -p "$STORAGE_BASE/data" "$STORAGE_BASE/workspace"
# shellcheck disable=SC2086
docker run --rm -d \
    --name auroracoder-agent-gpu \
    $GPU_ARG \
    $ENV_FILE_ARG \
    -e AURORACODER_DOCKER=1 \
    -e AURORACODER_VNC=1 \
    -e AURORACODER_GPU=1 \
    -v "$STORAGE_BASE/data:/app/data" \
    -v "$STORAGE_BASE/workspace:/workspace" \
    -p "$BACKEND_PORT":8080 \
    -p "$FRONTEND_PORT":3000 \
    -p "$VNC_PORT":6080 \
    -p "$TOOLSTORE_PORT":8765 \
    -p "$DEV_PORT_START-$DEV_PORT_END":8900-8902 \
    auroracoder-gpu || {
    echo "Failed to start container."
    echo "If you see 'could not select device driver', GPU passthrough is not available."
    echo "  Linux: sudo apt install -y nvidia-container-toolkit && sudo systemctl restart docker"
    echo "  Windows: ensure Docker Desktop uses WSL2 backend"
    exit 1
}
echo "Container started."
echo ""
echo "AuroraCoder GPU is running at http://localhost:$FRONTEND_PORT"
echo "To stop: docker stop auroracoder-agent-gpu"
echo ""
echo "Verify GPU inside container:"
echo "  docker exec auroracoder-agent-gpu python -c \"import torch; print(torch.cuda.get_device_name(0))\""
echo ""
echo "Opening browser..."
(sleep 3 && open_browser "http://localhost:$FRONTEND_PORT") &
