#!/usr/bin/env bash
# Finish VPS clip worker install — run as root on Oracle VPS
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/retro-movies}"
MOVIES_DIR="${MOVIES_DIR:-/opt/movies}"
APP_USER="${APP_USER:-niche}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
APP_PORT="${APP_PORT:-8766}"
ENV_FILE="${APP_ROOT}/.env"

if [[ -z "$WEBHOOK_SECRET" && -f "$ENV_FILE" ]]; then
  WEBHOOK_SECRET="$(grep -E '^WEBHOOK_SECRET=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
fi

if [[ -z "$WEBHOOK_SECRET" ]]; then
  WEBHOOK_SECRET="$(openssl rand -hex 32)"
  echo "Generated WEBHOOK_SECRET=$WEBHOOK_SECRET"
  echo "Add to GitHub: gh secret set VPS_WEBHOOK_SECRET --repo Battatawada/movie"
fi

mkdir -p "$MOVIES_DIR" "$APP_ROOT/runs"
chown -R "$APP_USER:$APP_USER" "$MOVIES_DIR" "$APP_ROOT/runs" 2>/dev/null || true

cat > "$APP_ROOT/.env" <<EOF
WEBHOOK_SECRET=${WEBHOOK_SECRET}
MOVIES_DIR=${MOVIES_DIR}
RUNS_DIR=${APP_ROOT}/runs
APP_ROOT=${APP_ROOT}
NICHE_HOST=0.0.0.0
NICHE_PORT=${APP_PORT}
EOF

chown "$APP_USER:$APP_USER" "$APP_ROOT/.env"
chmod 600 "$APP_ROOT/.env"

cp "$APP_ROOT/deploy/retro-clip-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable retro-clip-worker
systemctl restart retro-clip-worker

echo "Worker status:"
systemctl status retro-clip-worker --no-pager || true
echo ""
echo "Health check:"
curl -sf "http://127.0.0.1:${APP_PORT}/health" || echo "Worker not responding yet"
echo ""
echo "GitHub secrets to set:"
echo "  VPS_WEBHOOK_URL=http://140.245.245.123:${APP_PORT}"
echo "  VPS_WEBHOOK_SECRET=${WEBHOOK_SECRET}"
