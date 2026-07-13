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

echo "[entrypoint] Xvfb pid=$XVFB_PID display=$DISPLAY"
echo "[entrypoint] starting Chrome Session MCP server on port ${MCP_PORT:-8765}"

exec python /app/server.py
