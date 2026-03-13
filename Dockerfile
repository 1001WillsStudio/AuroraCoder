FROM continuumio/miniconda3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    THINKTOOL_DOCKER=1

RUN mkdir -p /app /workspace /seed

WORKDIR /app

# ── System packages (GUI / VNC stack + common libs) ────────────────────────
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        fluxbox \
        novnc \
        websockify \
        supervisor \
        xterm \
        fonts-dejavu \
        fonts-liberation \
        dbus-x11 \
        libgtk-3-0 \
        libsdl2-2.0-0 \
        libsdl2-image-2.0-0 \
        libsdl2-mixer-2.0-0 \
        libsdl2-ttf-2.0-0 \
        git \
        curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    ln -s /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# ── Python environment ─────────────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt

RUN conda create -n agent python=3.12 pip -y && \
    conda run -n agent pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────
COPY . /app

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
RUN sed -i 's/\r$//' /etc/supervisor/conf.d/supervisord.conf

# Agent API + dev-server ports + noVNC
EXPOSE 8080 8888 8889 8890 6080

ENTRYPOINT ["/entrypoint.sh"]
