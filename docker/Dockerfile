FROM auroracoder-base

ENV AURORACODER_DOCKER=1 \
    AURORACODER_VNC=1


WORKDIR /app

# Frontend dependencies (cached unless package.json changes)
COPY frontend/package.json frontend/package-lock.json /app/frontend/
RUN cd /app/frontend && npm install

# ── ToolStore client (unstable — bumped independently of requirements.txt) ──
RUN (test -f /opt/venv/bin/pip && /opt/venv/bin/pip install --no-cache-dir agent-tool-store>=2.1.8) || pip install --no-cache-dir agent-tool-store>=2.1.8

# ── Cache-bust boundary: cached above, always rebuilt below ──

ARG CACHEBUST
COPY . /app

# ── Quick-test: clone dev branch of AgentToolStore and install client from source ──
# RUN git clone -b dev https://github.com/1001WillsStudio/AgentToolStore /tmp/AgentToolStore && \
#     cd /tmp/AgentToolStore/client && \
#     /opt/venv/bin/pip install --no-cache-dir -e .

# Build frontend (produces frontend/dist/)
RUN cd /app/frontend && npm run build


COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
RUN sed -i 's/\r$//' /etc/supervisor/conf.d/supervisord.conf

# Agent API + conversation history + dev-server ports + noVNC + ToolStore mgmt
# Agent API (8080), Frontend (3000), Gateway (8081 internal), dev-server + noVNC, ToolStore (8765)
EXPOSE 8080 3000 8900 8901 8902 6080 8765

ENTRYPOINT ["/entrypoint.sh"]
