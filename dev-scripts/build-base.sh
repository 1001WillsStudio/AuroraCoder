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
