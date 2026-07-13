#!/usr/bin/env bash
set -e

# Virtual display so Chrome runs headful (better against anti-bot than headless).
XVFB_RES="${XVFB_RES:-1920x1080x24}"
rm -f /tmp/.X99-lock 2>/dev/null || true
Xvfb :99 -screen 0 "$XVFB_RES" -ac -nolisten tcp &
XVFB_PID=$!

# Wait for the display to be ready.
for i in $(seq 1 30); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
    sleep 0.2
done

export DISPLAY=:99
mkdir -p "${LIVE_PROFILE:-/profile}"

# --- live interactive view: x11vnc on the Xvfb display + noVNC (websockify) ---
# Lets you watch AND control the browser from a web URL (manual login/CAPTCHA).
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet -bg \
    -o /tmp/x11vnc.log 2>/dev/null || echo "[entrypoint] x11vnc failed to start"

NOVNC_DIR=/usr/share/novnc
[ -d "$NOVNC_DIR" ] || NOVNC_DIR=/usr/share/webapps/novnc
websockify --web "$NOVNC_DIR" "${VIEW_PORT:-6081}" localhost:5900 \
    > /tmp/websockify.log 2>&1 &
echo "[entrypoint] noVNC live view on port ${VIEW_PORT:-6081} (display :99)"

echo "[entrypoint] Xvfb pid=$XVFB_PID display=$DISPLAY"
echo "[entrypoint] starting Chrome Session MCP server on port ${MCP_PORT:-8765}"

exec python /app/server.py
