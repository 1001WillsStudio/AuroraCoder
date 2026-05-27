#!/bin/bash
set -e

# ── GitHub auth via Personal Access Token ─────────────────────────────────
# Works for ANY repo on github.com — no per-project setup needed.
# Set GITHUB_TOKEN in your .env file.
if [ -n "$GITHUB_TOKEN" ]; then
    git config --global url."https://oauth2:${GITHUB_TOKEN}@github.com/".insteadOf \
        "https://github.com/"
    echo "GitHub auth: GITHUB_TOKEN configured for all github.com repos."
fi

# ── ToolStore client — always pull latest at container start ────────────
echo "ToolStore: updating client package..."
if conda run -n agent pip install --no-cache-dir --force-reinstall \
    git+https://github.com/Mrw33554432/AgentToolStore.git#subdirectory=client; then
    echo "ToolStore: client package updated."
else
    echo "WARNING: ToolStore client install failed (repo unreachable or no network). Using cached version if available."
fi
# Seed workspace from bind mount if present
if [ -d /seed ] && [ "$(ls -A /seed 2>/dev/null)" ]; then
    cp -r /seed/* /workspace/
    echo "Seeded workspace from /seed ($(ls /seed | wc -l) items)"
fi

# Seed ToolStore index (MCP servers, skills, public registry)
mkdir -p /app/data/toolstore
toolstore update || echo "ToolStore: update skipped (no network or registry unreachable)"

export DISPLAY=:99
echo "Starting agent + VNC desktop (noVNC at http://localhost:6080) ..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
