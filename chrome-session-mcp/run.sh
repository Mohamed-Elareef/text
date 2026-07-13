#!/usr/bin/env bash
#
# Build + (re)run the Chrome Session MCP container on the host that runs the
# desktop/GUI Chrome. Run this ON the server (as the user whose Chrome profile
# you want to mirror — here: root).
#
set -euo pipefail

# ---- config (override via env) ------------------------------------------- #
IMAGE="${IMAGE:-chrome-session-mcp}"
CONTAINER="${CONTAINER:-chrome-session-mcp}"
PORT="${PORT:-8765}"
# Bind address for the published port. We do NOT want it on a public interface.
# Default: the docker bridge gateway (e.g. 172.17.0.1) so a reverse proxy running
# in another container (Traefik/nginx) can reach it over the bridge, while it
# stays off the host's public interfaces. Falls back to 127.0.0.1 if the gateway
# can't be detected. Set BIND=0.0.0.0 only for quick direct local testing.
BRIDGE_GW="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
BIND="${BIND:-${BRIDGE_GW:-127.0.0.1}}"
VIEW_PORT="${VIEW_PORT:-6081}"
HOST_PROFILE_DIR="${HOST_PROFILE_DIR:-/root/.config/google-chrome}"
DATA_DIR="${DATA_DIR:-/opt/chrome-mcp}"
SOFT_SYNC_INTERVAL="${SOFT_SYNC_INTERVAL:-300}"

# Public base URL (through the reverse proxy) used for screenshot links + live view.
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://mcp.cloudstars.club/chrome}"
LIVE_VIEW_URL="${LIVE_VIEW_URL:-https://mcp.cloudstars.club/chrome-view/vnc.html?path=chrome-view/websockify&autoconnect=1&resize=scale}"
# Telegram bot for pushing screenshots (optional).
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

cd "$(dirname "$0")"

# ---- token: reuse existing, else generate + persist ---------------------- #
mkdir -p "$DATA_DIR"
TOKEN_FILE="$DATA_DIR/token"
if [[ -n "${MCP_TOKEN:-}" ]]; then
    echo "$MCP_TOKEN" > "$TOKEN_FILE"
elif [[ -f "$TOKEN_FILE" ]]; then
    MCP_TOKEN="$(cat "$TOKEN_FILE")"
else
    MCP_TOKEN="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    echo "$MCP_TOKEN" > "$TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"

if [[ ! -d "$HOST_PROFILE_DIR" ]]; then
    echo "ERROR: host Chrome profile not found at $HOST_PROFILE_DIR" >&2
    exit 1
fi

echo "==> Building image $IMAGE"
docker build -t "$IMAGE" .

echo "==> (Re)starting container $CONTAINER"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --shm-size=2g \
    -p "${BIND}:${PORT}:8765" \
    -p "${BIND}:${VIEW_PORT}:6081" \
    -v "${HOST_PROFILE_DIR}:/host-profile:ro" \
    -v "${DATA_DIR}/profile:/profile" \
    -e "MCP_TOKEN=${MCP_TOKEN}" \
    -e "MCP_PORT=8765" \
    -e "SOFT_SYNC_INTERVAL=${SOFT_SYNC_INTERVAL}" \
    -e "PUBLIC_BASE_URL=${PUBLIC_BASE_URL}" \
    -e "LIVE_VIEW_URL=${LIVE_VIEW_URL}" \
    -e "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}" \
    -e "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}" \
    "$IMAGE"

# Public endpoint served through the nginx + Cloudflare reverse proxy (see
# deploy/nginx-chrome.conf). The container port itself is bound to $BIND only.
PUBLIC_URL="${PUBLIC_URL:-https://mcp.cloudstars.club/chrome}"
cat <<EOF

==========================================================================
 Chrome Session MCP is running.
   Container : $CONTAINER  (ports bound to ${BIND}, not public)
   Endpoint  : ${PUBLIC_URL}/mcp
   Health    : ${PUBLIC_URL}/health
   Live view : ${LIVE_VIEW_URL}
   Token     : ${MCP_TOKEN}
   (token saved at ${TOKEN_FILE})

 Add to Claude Code:
   claude mcp add --transport http chrome-session \\
       ${PUBLIC_URL}/mcp \\
       --header "Authorization: Bearer ${MCP_TOKEN}"

 Logs:  docker logs -f ${CONTAINER}
==========================================================================
EOF
