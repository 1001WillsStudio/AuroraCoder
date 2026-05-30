#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "========================================"
echo "  Build AuroraCoder Base Image"
echo "  (Node.js + Python env + ToolStore)"
echo "========================================"
echo ""

# Read GITHUB_TOKEN from .env for ToolStore
GITHUB_TOKEN=$(grep '^GITHUB_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
if [ -z "$GITHUB_TOKEN" ]; then
    echo "WARNING: GITHUB_TOKEN not found in .env — ToolStore install may be skipped."
fi

echo "[base] Building base image..."
docker build -t thinkwithtool-base -f docker/Dockerfile.base --build-arg GITHUB_TOKEN="$GITHUB_TOKEN" .
echo "[base] Done."
