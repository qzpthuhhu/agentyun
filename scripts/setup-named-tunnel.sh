#!/usr/bin/env bash
# setup-named-tunnel.sh — programmatic named tunnel setup.
#
# Reads API token + account ID from environment (or interactive prompt).
# Avoids the browser-based `cloudflared tunnel login` flow.
#
# Usage:
#   CLOUDFLARE_API_TOKEN=xxx CLOUDFLARE_ACCOUNT_ID=yyy \
#     ./scripts/setup-named-tunnel.sh [DOMAIN]
#
# If no DOMAIN is provided, falls back to the cfargotunnel.com subdomain
# (no DNS setup needed — works immediately, fixed URL like
# https://&lt;tunnel-id&gt;.cfargotunnel.com).

set -euo pipefail

TUNNEL_NAME="${AGENTCLOUD_TUNNEL:-agentcloud}"
DOMAIN="${1:-${AGENTCLOUD_DOMAIN:-}}"

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
    echo "Need CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID env vars." >&2
    echo "Get them at: https://dash.cloudflare.com/profile/api-tokens" >&2
    exit 1
fi

API="https://api.cloudflare.com/client/v4"
AUTH=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}")

echo "==> 1. Verify API token"
curl -fsS "${AUTH[@]}" -H "Content-Type: application/json" \
    "$API/user" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if not d.get('success'):
    print('ERROR:', d.get('errors')); sys.exit(1)
u = d['result']
print(f\"  OK — {u['email']} (id={u['id']})\")
"

echo
echo "==> 2. Check if tunnel '$TUNNEL_NAME' already exists"
EXISTING=$(curl -fsS "${AUTH[@]}" "$API/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel?name=$TUNNEL_NAME" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['result'][0]['id'] if d.get('success') and d.get('result') else '')")
if [ -n "$EXISTING" ]; then
    echo "  Found existing tunnel: $EXISTING"
    TUNNEL_ID="$EXISTING"
else
    echo "  Creating new tunnel..."
    RESP=$(curl -fsS "${AUTH[@]}" -H "Content-Type: application/json" \
        -X POST "$API/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel" \
        -d "{\"name\":\"$TUNNEL_NAME\",\"config_src\":\"cloudflare\"}")
    TUNNEL_ID=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['id'])")
    echo "  Created: $TUNNEL_ID"
fi

echo
echo "==> 3. Get tunnel token (used as credentials file)"
TOKEN_RESP=$(curl -fsS "${AUTH[@]}" "$API/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/$TUNNEL_ID/token")
TOKEN_B64=$(echo "$TOKEN_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['result'])" | base64 -d | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)))")

mkdir -p ~/.cloudflared
echo "$TOKEN_B64" > ~/.cloudflared/$TUNNEL_ID.json
echo "  Token saved to ~/.cloudflared/$TUNNEL_ID.json"

echo
echo "==> 4. Pick URL"
if [ -n "$DOMAIN" ]; then
    URL="https://$DOMAIN"

    echo "  Custom domain: $URL"
    # Check zone exists
    ZONE_RESP=$(curl -fsS "${AUTH[@]}" "$API/zones?name=$DOMAIN")
    ZONE_ID=$(echo "$ZONE_RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
zones = d.get('result', [])
# Match root or any subdomain
for z in zones:
    if DOMAIN.endswith(z['name']) or DOMAIN == z['name']:
        print(z['id']); break
" DOMAIN="$DOMAIN")
    if [ -z "$ZONE_ID" ]; then
        echo "  WARNING: no Cloudflare zone found for $DOMAIN"
        echo "  Add your domain to Cloudflare first, then re-run."
        echo "  Falling back to cfargotunnel.com subdomain for now."
        URL="https://${TUNNEL_ID}.cfargotunnel.com"
        DOMAIN=""
    else
        echo "  Zone ID: $ZONE_ID"
    fi
fi

if [ -z "$DOMAIN" ]; then
    URL="https://${TUNNEL_ID}.cfargotunnel.com"
    echo "  Default URL (no custom domain): $URL"
fi

echo
echo "==> 5. Write cloudflared config"
cat > ~/.cloudflared/config.yml <<YAML
tunnel: $TUNNEL_ID
credentials-file: $HOME/.cloudflared/$TUNNEL_ID.json

ingress:
  - service: http://127.0.0.1:18000
YAML
echo "  Config written."

# If we have a custom domain, add ingress rule + DNS record
if [ -n "$DOMAIN" ]; then
    cat > ~/.cloudflared/config.yml <<YAML
tunnel: $TUNNEL_ID
credentials-file: $HOME/.cloudflared/$TUNNEL_ID.json

ingress:
  - hostname: $DOMAIN
    service: http://127.0.0.1:18000
  - service: http_status:404
YAML

    echo
    echo "==> 6. Create DNS CNAME record for $DOMAIN"
    DNS_RESP=$(curl -fsS "${AUTH[@]}" -H "Content-Type: application/json" \
        -X POST "$API/zones/$ZONE_ID/dns_records" \
        -d "{\"type\":\"CNAME\",\"name\":\"$(echo $DOMAIN | sed "s/.${zones_name}//")\",\"content\":\"$TUNNEL_ID.cfargotunnel.com\",\"proxied\":true}")
    echo "  DNS record created (or already exists)"
fi

echo
echo "==> Done. Your stable AgentCloud URL is:"
echo
echo "    $URL"
echo
echo "==> To start the tunnel:"
echo
echo "    cloudflared tunnel run $TUNNEL_NAME"
echo
echo "    (foreground; Ctrl+C to stop)"
echo "    (or run in background: nohup cloudflared tunnel run $TUNNEL_NAME > /tmp/agentcloud-tunnel.log 2>&1 &)"
echo
echo "==> Update docs/app.html:"
echo "    <meta name=\"agentcloud-server\" content=\"$URL\">"
echo
echo "==> Save these for next session:"
echo "    TUNNEL_ID=$TUNNEL_ID"
echo "    URL=$URL"