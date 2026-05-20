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
cd "$SCRIPT_DIR"

# ── Instance configuration ──────────────────────────────────────────────
# Auto-detects the next free instance number (2, 3, 4, …).
# Or pass one explicitly:  ./another-one.sh 5
INST="${1:-}"
if [ -n "$INST" ]; then
    :  # explicit instance number provided
else
    INST=2
    while docker inspect --format="." "thinkwithtool-agent-$INST" >/dev/null 2>&1; do
        INST=$((INST + 1))
    done
fi

# Port arithmetic — each instance offsets from the base by (INST-1)*2
OFFSET=$(( (INST - 1) * 2 ))
BACKEND_PORT=$((8080 + OFFSET))
GATEWAY_PORT=$((8081 + OFFSET))
VNC_PORT=$((6080 + OFFSET))
DEV_PORT_START=$((8888 + OFFSET * 3 / 2))
DEV_PORT_END=$((DEV_PORT_START + 2))
FRONTEND_PORT=$((3000 + INST - 1))

CONTAINER="thinkwithtool-agent-$INST"

# ── Storage base ────────────────────────────────────────────────────────
if [ -d "$HOME/Documents" ]; then
    STORAGE_BASE="$HOME/Documents/ThinkTool"
elif [ -d "$HOME/documents" ]; then
    STORAGE_BASE="$HOME/documents/ThinkTool"
else
    STORAGE_BASE="$HOME/ThinkTool"
fi
DATA_DIR="$STORAGE_BASE/data-$INST"
WORKSPACE_DIR="$STORAGE_BASE/workspace-$INST"

echo "========================================"
echo "  AuroraCoder  [Instance $INST]"
echo "========================================"
echo "  Backend API:    http://localhost:$BACKEND_PORT"
echo "  Convo History:  http://localhost:$GATEWAY_PORT"
echo "  API Docs:       http://localhost:$BACKEND_PORT/docs"
echo "  Frontend:       http://0.0.0.0:$FRONTEND_PORT"
echo "  VNC Desktop:    http://localhost:$VNC_PORT"
echo "========================================"
echo ""

# ── Pre-flight checks ───────────────────────────────────────────────────
# The base + app images must already exist (built by start.sh)
if ! docker inspect --type=image thinkwithtool >/dev/null 2>&1; then
    echo "ERROR: App image \"thinkwithtool\" not found."
    echo "Run start.sh first to build the Docker images."
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Create it with your API keys."
    echo "See .env.example for the required variables."
    exit 1
fi

# ── Build a filtered .env without GITHUB_TOKEN ──────────────────────────
GUEST_ENV="$PWD/.env.guest-$INST"
grep -vi "GITHUB_TOKEN" .env > "$GUEST_ENV"

# ── Data directories ────────────────────────────────────────────────────
mkdir -p "$DATA_DIR" "$WORKSPACE_DIR"

# ── Stop old container if any ───────────────────────────────────────────
echo "Stopping old container \"$CONTAINER\" if any..."
docker stop "$CONTAINER" >/dev/null 2>&1 || true
docker rm   "$CONTAINER" >/dev/null 2>&1 || true

# ── Start backend container ─────────────────────────────────────────────
echo "[1/2] Starting backend in Docker (instance $INST)..."
docker run --rm -d \
    --name "$CONTAINER" \
    --env-file "$GUEST_ENV" \
    -e THINKTOOL_DOCKER=1 \
    -e THINKTOOL_VNC=1 \
    -v "$DATA_DIR:/app/data" \
    -v "$WORKSPACE_DIR:/workspace" \
    -p "$BACKEND_PORT:8080" \
    -p "$GATEWAY_PORT:8081" \
    -p "$VNC_PORT:6080" \
    -p "$DEV_PORT_START-$DEV_PORT_END:8888-8890" \
    thinkwithtool || {
    rm -f "$GUEST_ENV"
    echo "Failed to start container."
    exit 1
}
rm -f "$GUEST_ENV"
echo "Container \"$CONTAINER\" started (API :$BACKEND_PORT + gateway :$GATEWAY_PORT)."
echo ""

# ── Frontend ────────────────────────────────────────────────────────────
# Reuse the same node_modules from the primary frontend — no extra install needed.
# Override the Vite dev-server port and proxy targets via environment variables.
echo "[2/2] Starting frontend on http://0.0.0.0:$FRONTEND_PORT ..."
echo "Press Ctrl+C to stop the frontend."
echo "To stop the backend:  docker stop $CONTAINER"
echo ""

cd frontend
VITE_PORT="$FRONTEND_PORT" \
VITE_BACKEND_PORT="$BACKEND_PORT" \
VITE_GATEWAY_PORT="$GATEWAY_PORT" \
npm run dev -- --host 0.0.0.0
