# Deployment Guide

How to deploy AgentCloud with a public URL so other devices and users can reach it.

## Architecture

```
User device (anywhere)                      Your Mac (or any host)
     │                                            │
     │  HTTPS                                     │
     ▼                                            │
[Cloudflare Edge] ──QUIC/HTTP2──▶ [cloudflared] ──HTTP──▶ [AgentCloud :18000]
                                                       (FastAPI + SQLite)
```

For production you'd put AgentCloud on a real cloud server and use a **named** Cloudflare Tunnel (persistent), but this guide covers the **quick tunnel** path that works in <2 minutes.

## Prerequisites

- macOS or Linux with `cloudflared` installed (`brew install cloudflared`)
- Python 3.9+ with the AgentCloud packages installed (SDK + CLI + Cloud)
- Ports 18000 free on the host

## Step 1: Start the cloud service

```bash
cd /path/to/agentcloud/packages/cloud
( uvicorn app.main:app --port 18000 --host 127.0.0.1 > /tmp/agentcloud-cloud.log 2>&1 & disown $! )
```

Verify:
```bash
curl http://127.0.0.1:18000/healthz
# → {"status":"ok"}
```

## Step 2: Start a Cloudflare quick tunnel

```bash
( cloudflared tunnel --url http://127.0.0.1:18000 --no-autoupdate --protocol http2 --edge-ip-version 4 > /tmp/cloudflared.log 2>&1 & disown $! )
```

Watch the log for the assigned URL:
```bash
tail -f /tmp/cloudflared.log
# ...
# INF |  Your quick Tunnel has been created! Visit it at:
# INF |  https://vatican-excerpt-terminal-planes.trycloudflare.com
```

The `https://*.trycloudflare.com` URL is your **public address**. Anyone can hit it.

**Note**: The URL changes every time cloudflared restarts. See "Stable URL" below.

## Step 3: Update the website's server URL

Edit `docs/app.html` (in the agentcloud repo):

```html
<meta name="agentcloud-server" content="https://YOUR-NEW-URL.trycloudflare.com">
```

Then push:
```bash
cd /path/to/agentcloud
git add docs/app.html
git commit -m "Update tunnel URL"
git push
```

GitHub Pages will rebuild in ~30s. Now the "申请 Key" form on https://qzpthuhhu.github.io/agentcloud/app.html will POST to your real cloud service.

## Step 4: Verify end-to-end

From any device on the internet:
```bash
curl https://YOUR-URL.trycloudflare.com/healthz
# → {"status":"ok"}

curl -X POST https://YOUR-URL.trycloudflare.com/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"label":"my-agent"}'
# → {"key":"...","key_id":"...","recovery_code":"...","created_at":"..."}
```

Or open the website in a browser and click "申请 Key" — you'll get a real key back, ready to use:

```bash
agentcloud login --key <KEY>
agentcloud memory add "Hello from another device" --type fact
agentcloud sync daemon --start
```

## Stable URL (named tunnel)

Quick tunnels rotate URLs. For a persistent URL:

1. Sign up at https://dash.cloudflare.com (free tier is enough)
2. Install cloudflared and login: `cloudflared tunnel login`
3. Create a named tunnel:
   ```bash
   cloudflared tunnel create agentcloud
   ```
4. Configure `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: <TUNNEL_ID>
   credentials-file: /Users/you/.cloudflared/<TUNNEL_ID>.json
   ingress:
     - hostname: agentcloud.yourdomain.com
       service: http://127.0.0.1:18000
     - service: http_status:404
   ```
5. Add a CNAME in Cloudflare DNS pointing to `<TUNNEL_ID>.cfargotunnel.com`
6. Run: `cloudflared tunnel run agentcloud`

Now you have a stable URL like `https://agentcloud.yourdomain.com`.

## Production deployment (real cloud server)

For long-running, multi-user production use:

1. Get a small VPS (Tencent Cloud / Aliyun Lightweight / DigitalOcean — $5/mo)
2. SSH in, install Docker
3. `git clone` this repo and `docker compose up -d`
4. Set up a named Cloudflare Tunnel pointing to the VPS
5. Set strong `AGENTYUN_JWT_SECRET` env var

## Troubleshooting

**Tunnel URL changes after restart**: That's how quick tunnels work. Either update `app.html` each time, or set up a named tunnel.

**Tunnel shows 502**: Origin (cloud) is down. Check `tail -f /tmp/agentcloud-cloud.log` and restart if needed.

**CORS errors in browser**: Make sure `app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)` in `app/main.py` — it does. Should not be an issue for `app.html` calling the cloud.

**Tunnel gets stuck on `--protocol quic`**: Use `--protocol http2 --edge-ip-version 4` as shown above; some networks block QUIC.
