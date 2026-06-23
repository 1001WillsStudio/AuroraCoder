#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "========================================"
echo "  Build AuroraCoder Base Image"
echo "  (Node.js + Python env + ToolStore)"
echo "========================================"
echo ""


echo "[base] Building base image..."
docker build -t auroracoder-base -f docker/Dockerfile.base .
echo "[base] Done."

echo ""
echo "[gpu-base] Building GPU base image (PyTorch + CUDA, this may take a few minutes)..."
docker build -t auroracoder-gpu-base -f docker/Dockerfile.gpu-base .
echo "[gpu-base] Done."

echo ""
echo "All base images built. Run start.sh or gpu.sh to start."
