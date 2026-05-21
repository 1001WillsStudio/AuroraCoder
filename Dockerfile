FROM thinkwithtool-base

ENV THINKTOOL_DOCKER=1 \
    THINKTOOL_VNC=1

WORKDIR /app

# Node.js 20.x for frontend build (Vite requires Node >= 18)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Application source (only layer that changes between rebuilds) ─────────
COPY requirements.txt /app/requirements.txt
RUN conda run -n agent pip install --no-cache-dir -r requirements.txt

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

# Agent API + conversation history + dev-server ports + noVNC
EXPOSE 8080 8081 8900 8901 8902 6080

ENTRYPOINT ["/entrypoint.sh"]
