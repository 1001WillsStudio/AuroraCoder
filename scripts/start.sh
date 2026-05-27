#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AuroraCoder — One-click launcher for Linux & macOS
# Equivalent to start.bat for Windows.
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "========================================"
echo "  AuroraCoder"
echo "========================================"
echo "  Frontend:       http://localhost:3000"
echo "  Backend API:    http://localhost:8080"
echo "  API Docs:       http://localhost:8080/docs"
echo "  VNC Desktop:    http://localhost:6080"
echo "  ToolStore:      http://localhost:8765"
echo "========================================"
echo ""

# ── Read GITHUB_TOKEN from .env for ToolStore (used in base image build)
GITHUB_TOKEN=$(grep '^GITHUB_TOKEN=' .env 2>/dev/null | cut -d= -f2-)

# ── Check if base image exists; build if missing ─────────────────────────
if docker inspect --type=image thinkwithtool-base >/dev/null 2>&1; then
    echo "[base] Base image found, skipping."
else
    echo "[base] Building base image -- first time, this may take a few minutes..."
    docker build -t thinkwithtool-base -f docker/Dockerfile.base --build-arg GITHUB_TOKEN="$GITHUB_TOKEN" . || {
        echo "Base image build failed."
        exit 1
    }
    echo "[base] Done."
fi

# ── Always rebuild app image (fast: just copies source code) ─────────────
# Generate unique cache-bust key to force ToolStore reinstall every run
CACHEBUST=$(date +%s)
echo "[app] Building app image (cache-bust: $CACHEBUST)..."
docker build -t thinkwithtool --build-arg GITHUB_TOKEN="$GITHUB_TOKEN" --build-arg CACHEBUST="$CACHEBUST" -f docker/Dockerfile . || {
    echo "App image build failed."
    exit 1
}

# ── Stop existing container if running ───────────────────────────────────
echo "Stopping old container if any..."
docker stop thinkwithtool-agent >/dev/null 2>&1 || true
docker rm thinkwithtool-agent >/dev/null 2>&1 || true

# ── Storage base — all persistent data lives under Documents/ThinkTool ────
# Uses platform-appropriate Documents path
if [ -d "$HOME/Documents" ]; then
    STORAGE_BASE="$HOME/Documents/ThinkTool"
elif [ -d "$HOME/documents" ]; then
    # Some Linux distros use lowercase
    STORAGE_BASE="$HOME/documents/ThinkTool"
else
    # Fallback: just use home directory
    STORAGE_BASE="$HOME/ThinkTool"
fi

# ── Verify .env file exists ──────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Create it with your API keys."
    echo "See .env.example for the required variables."
    exit 1
fi

# ── Start backend container ──────────────────────────────────────────────
echo "Starting backend in Docker (app + frontend)..."
mkdir -p "$STORAGE_BASE/data" "$STORAGE_BASE/workspace"
docker run --rm -d \
    --name thinkwithtool-agent \
    --env-file .env \
    -e THINKTOOL_DOCKER=1 \
    -e THINKTOOL_VNC=1 \
    -v "$STORAGE_BASE/data:/app/data" \
    -v "$STORAGE_BASE/workspace:/workspace" \
    -p 8080:8080 \
    -p 3000:3000 \
    -p 6080:6080 \
    -p 8765:8765 \
    -p 8900-8902:8900-8902 \
    thinkwithtool || {
    echo "Failed to start container."
    exit 1
}
echo "Container started."
echo ""
echo "AuroraCoder is running at http://localhost:3000"
echo "To stop: docker stop thinkwithtool-agent"
