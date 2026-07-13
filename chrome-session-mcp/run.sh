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
HOST_PROFILE_DIR="${HOST_PROFILE_DIR:-/root/.config/google-chrome}"
DATA_DIR="${DATA_DIR:-/opt/chrome-mcp}"
SOFT_SYNC_INTERVAL="${SOFT_SYNC_INTERVAL:-300}"

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
    -p "${PORT}:8765" \
    -v "${HOST_PROFILE_DIR}:/host-profile:ro" \
    -v "${DATA_DIR}/profile:/profile" \
    -e "MCP_TOKEN=${MCP_TOKEN}" \
    -e "MCP_PORT=8765" \
    -e "SOFT_SYNC_INTERVAL=${SOFT_SYNC_INTERVAL}" \
    "$IMAGE"

PUBLIC_IP="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo '<SERVER_IP>')"
cat <<EOF

==========================================================================
 Chrome Session MCP is running.
   Container : $CONTAINER
   Endpoint  : http://${PUBLIC_IP}:${PORT}/mcp
   Health    : http://${PUBLIC_IP}:${PORT}/health
   Token     : ${MCP_TOKEN}
   (token saved at ${TOKEN_FILE})

 Add to Claude Code:
   claude mcp add --transport http chrome-session \\
       http://${PUBLIC_IP}:${PORT}/mcp \\
       --header "Authorization: Bearer ${MCP_TOKEN}"

 Logs:  docker logs -f ${CONTAINER}
==========================================================================
EOF
