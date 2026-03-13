#!/bin/bash
set -e

# Seed workspace from bind mount if present
if [ -d /seed ] && [ "$(ls -A /seed 2>/dev/null)" ]; then
    cp -r /seed/* /workspace/
    echo "Seeded workspace from /seed ($(ls /seed | wc -l) items)"
fi

# ── VNC mode (enabled at runtime via THINKTOOL_VNC=1) ──────────────────────
if [ "$THINKTOOL_VNC" = "1" ]; then
    export DISPLAY=:99
    echo "Starting with VNC desktop (noVNC at http://localhost:6080) ..."
    exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
fi

# ── Standard mode (no VNC) ─────────────────────────────────────────────────
exec conda run --no-capture-output -n agent python run_web.py
