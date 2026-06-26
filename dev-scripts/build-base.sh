#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "=== Build AuroraCoder Base Images ==="

echo "[base] Building CPU base..."
docker build -t auroracoder-base -f docker/Dockerfile.base .
echo "[base] Done."

NV_IMAGE="nvcr.io/nvidia/vllm:26.05.post1-py3"
if docker inspect --type=image "$NV_IMAGE" >/dev/null 2>&1; then
    echo "[nv] NVIDIA vLLM image already cached."
else
    echo "[nv] Pulling NVIDIA vLLM image (one time only, ~9 GB)..."
    docker pull "$NV_IMAGE"
fi

echo "[gpu-base] Building GPU base..."
docker build -t auroracoder-gpu-base -f docker/Dockerfile.gpu-base .
echo "[gpu-base] Done."
echo "All base images built."
