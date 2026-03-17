#!/bin/bash
set -e

# Seed workspace from bind mount if present
if [ -d /seed ] && [ "$(ls -A /seed 2>/dev/null)" ]; then
    cp -r /seed/* /workspace/
    echo "Seeded workspace from /seed ($(ls /seed | wc -l) items)"
fi

export DISPLAY=:99
echo "Starting agent + VNC desktop (noVNC at http://localhost:6080) ..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
