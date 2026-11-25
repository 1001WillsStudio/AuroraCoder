#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

GPU_FLAG="--gpus"
GPU_DEVICE="${AURORACODER_GPU_DEVICE:-all}"
GPU_ARG="$GPU_FLAG $GPU_DEVICE"

echo "[gpu] GPU argument: $GPU_ARG"

# Stop old container
echo "Stopping old container..."
docker stop auroracoder-agent-gpu >/dev/null 2>&1 || true
docker rm auroracoder-agent-gpu >/dev/null 2>&1 || true
sleep 2

# Ports
FRONTEND_PORT=3000; BACKEND_PORT=8080; VNC_PORT=6080; TOOLSTORE_PORT=8765
DEV_PORT_START=8900; DEV_PORT_END=8902
[ -f ports.conf ] && while IFS='=' read -r k v; do
    case "$k" in FRONTEND_PORT) FRONTEND_PORT="$v";; BACKEND_PORT) BACKEND_PORT="$v";;
        VNC_PORT) VNC_PORT="$v";; TOOLSTORE_PORT) TOOLSTORE_PORT="$v";;
        DEV_PORT_START) DEV_PORT_START="$v";; DEV_PORT_END) DEV_PORT_END="$v";; esac
done < <(grep -v '^#' ports.conf 2>/dev/null | grep -v '^$')

source "$SCRIPT_DIR/port-availability.sh"
resolve_auroracoder_ports

echo "=== AuroraCoder GPU ==="
echo "  Frontend:     http://localhost:$FRONTEND_PORT"
echo "  Backend API:  http://localhost:$BACKEND_PORT"
echo "  VNC Desktop:  http://localhost:$VNC_PORT"
echo "  ToolStore:    http://localhost:$TOOLSTORE_PORT"

STORAGE_BASE="${HOME}/Documents/AuroraCoder-GPU"
[ -d "$HOME/Documents" ] || STORAGE_BASE="${HOME}/AuroraCoder-GPU"

# ── Pre-pull NVIDIA vLLM image (~9 GB, one time only) ────────────────────
NV_IMAGE="nvcr.io/nvidia/vllm:26.05.post1-py3"
if docker inspect --type=image "$NV_IMAGE" >/dev/null 2>&1; then
    echo "[nv] NVIDIA vLLM image already cached."
else
    echo "[nv] Pulling NVIDIA vLLM image (one time only, ~9 GB)..."
    docker pull "$NV_IMAGE" || { echo "Pull failed."; exit 1; }
fi

# ── Build GPU base ───────────────────────────────────────────────────────
if docker inspect --type=image auroracoder-gpu-base >/dev/null 2>&1; then
    echo "[gpu-base] Found, skipping."
else
    echo "[gpu-base] Building..."
    docker build -t auroracoder-gpu-base -f docker/Dockerfile.gpu-base . || { echo "Build failed."; exit 1; }
    echo "[gpu-base] Done."
fi

# ── Build GPU app ────────────────────────────────────────────────────────
echo "[gpu] Building app image..."
docker build -t auroracoder-gpu -f docker/Dockerfile.gpu . || { echo "Build failed."; exit 1; }

# ── Start container ──────────────────────────────────────────────────────
ENV_FILE_ARG=""; [ -f .env ] && ENV_FILE_ARG="--env-file .env" || echo "NOTE: .env not found, configure keys via Settings UI."
mkdir -p "$STORAGE_BASE/data" "$STORAGE_BASE/workspace"
docker run --rm -d --name auroracoder-agent-gpu \
    $GPU_ARG $ENV_FILE_ARG \
    -e AURORACODER_DOCKER=1 -e AURORACODER_VNC=1 -e AURORACODER_GPU=1 \
    -v "$STORAGE_BASE/data:/app/data" -v "$STORAGE_BASE/workspace:/workspace" \
    -p $BACKEND_PORT:8080 -p $FRONTEND_PORT:3000 -p $VNC_PORT:6080 \
    -p $TOOLSTORE_PORT:8765 -p $DEV_PORT_START-$DEV_PORT_END:8900-8902 \
    auroracoder-gpu || { echo "Container failed."; exit 1; }

echo "Waiting for AuroraCoder services..."
docker exec auroracoder-agent-gpu python3 /app/src/readiness.py || {
    echo "Check startup with docker logs auroracoder-agent-gpu"
    exit 1
}

echo "Running at http://localhost:$FRONTEND_PORT"
echo "Verify: docker exec auroracoder-agent-gpu python -c \"import torch; print(torch.cuda.get_device_name(0))\""
