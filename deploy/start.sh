#!/bin/bash
# Runs the office (loopback only) and the Cloudflare Tunnel side by side.
# If either dies the machine exits and Fly restarts it.
set -euo pipefail
: "${TUNNEL_TOKEN:?run: fly secrets set TUNNEL_TOKEN=...}"
: "${ONSET_OFFICE_KEY:?run: fly secrets set ONSET_OFFICE_KEY=...}"

mkdir -p /data && chown -R appuser:appuser /data
# setpriv keeps root's HOME; point it at appuser's so pollers that look in
# ~/.claude don't hit /root (PermissionError spam every second).
export HOME=/home/appuser
as_app() { setpriv --reuid=appuser --regid=appuser --init-groups "$@"; }

cd /app
# --no-proxy-headers keeps request.client as 127.0.0.1 so LOCALHOST_ONLY
# still holds: nothing but the local tunnel can reach the app.
as_app /app/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers &
# cloudflared reads TUNNEL_TOKEN from the environment (kept out of `ps`).
as_app cloudflared --no-autoupdate tunnel run &

wait -n
exit 1
