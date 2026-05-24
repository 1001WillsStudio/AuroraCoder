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

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
RUN sed -i 's/\r$//' /etc/supervisor/conf.d/supervisord.conf

# Agent API + conversation history + dev-server ports + noVNC + ToolStore mgmt
# Agent API (8080), Frontend (3000), Gateway (8081 internal), dev-server + noVNC, ToolStore (8765)
EXPOSE 8080 3000 8900 8901 8902 6080 8765

ENTRYPOINT ["/entrypoint.sh"]
