FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# --- system deps + Google Chrome (stable) --------------------------------- #
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates rsync xvfb x11-utils dbus-x11 \
        x11vnc novnc websockify \
        fonts-liberation fonts-noto-color-emoji fonts-noto-cjk fonts-kacst \
        procps tini \
    && wget -q -O /tmp/chrome-key.pub https://dl.google.com/linux/linux_signing_key.pub \
    && gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg /tmp/chrome-key.pub \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* /tmp/chrome-key.pub

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY server.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV HOST_PROFILE=/host-profile \
    LIVE_PROFILE=/profile \
    CHROME_CHANNEL=chrome \
    MCP_PORT=8765 \
    VIEW_PORT=6081 \
    DISPLAY=:99

EXPOSE 8765 6081
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
