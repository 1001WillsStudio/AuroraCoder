FROM thinkwithtool-base

ENV THINKTOOL_DOCKER=1 \
    THINKTOOL_VNC=1


WORKDIR /app

# Frontend dependencies (cached unless package.json changes)
COPY frontend/package.json frontend/package-lock.json /app/frontend/
RUN cd /app/frontend && npm install

COPY . /app

# Build frontend (produces frontend/dist/)
RUN cd /app/frontend && npm run build

# ── ToolStore client — always pull latest from git (LAST layer = cache-bust cheap) ──
ARG GITHUB_TOKEN=
ARG CACHEBUST=0
RUN ( echo "ToolStore cache-bust: ${CACHEBUST}" && \
    if [ -n "${GITHUB_TOKEN}" ]; then \
        echo "ToolStore: installing with GitHub token..." && \
        conda run -n agent pip install --no-cache-dir --force-reinstall \
          "git+https://x-access-token:${GITHUB_TOKEN}@github.com/Mrw33554432/AgentToolStore.git#subdirectory=client"; \
    else \
        echo "ToolStore: no GITHUB_TOKEN, trying anonymous clone..." && \
        conda run -n agent pip install --no-cache-dir --force-reinstall \
          git+https://github.com/Mrw33554432/AgentToolStore.git#subdirectory=client; \
    fi ) || echo "WARNING: ToolStore install skipped (repo is private or unreachable)."

COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
RUN sed -i 's/\r$//' /etc/supervisor/conf.d/supervisord.conf

# Agent API + conversation history + dev-server ports + noVNC + ToolStore mgmt
# Agent API (8080), Frontend (3000), Gateway (8081 internal), dev-server + noVNC, ToolStore (8765)
EXPOSE 8080 3000 8900 8901 8902 6080 8765

ENTRYPOINT ["/entrypoint.sh"]
