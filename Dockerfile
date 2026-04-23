FROM thinkwithtool-base

ENV THINKTOOL_DOCKER=1 \
    THINKTOOL_VNC=1

WORKDIR /app

# ── Application source (only layer that changes between rebuilds) ─────────
COPY . /app

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
RUN sed -i 's/\r$//' /etc/supervisor/conf.d/supervisord.conf

# Agent API + conversation history + dev-server ports + noVNC
EXPOSE 8080 8081 8888 8889 8890 6080

ENTRYPOINT ["/entrypoint.sh"]
