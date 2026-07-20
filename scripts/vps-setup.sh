#!/usr/bin/env bash
# Oracle VPS bootstrap for Retro Movie Archive clip worker
set -euo pipefail

APP_ROOT="${APP_ROOT:-/opt/retro-movies}"
APP_REPO_URL="${APP_REPO_URL:-https://github.com/Battatawada/movie.git}"
MOVIES_DIR="${MOVIES_DIR:-/opt/movies}"
APP_USER="${APP_USER:-niche}"
APP_PORT="${APP_PORT:-8766}"

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-pip python3-venv ffmpeg curl git ufw

echo "==> Movies library at ${MOVIES_DIR}"
mkdir -p "${MOVIES_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${MOVIES_DIR}" 2>/dev/null || true

echo "==> Clone/update repo at ${APP_ROOT}"
mkdir -p "$(dirname "$APP_ROOT")"
if [[ -d "$APP_ROOT/.git" ]]; then
  git -C "$APP_ROOT" pull --ff-only
elif [[ ! -d "$APP_ROOT" ]] || [[ -z "$(ls -A "$APP_ROOT" 2>/dev/null)" ]]; then
  git clone "$APP_REPO_URL" "$APP_ROOT"
fi
chown -R "${APP_USER}:${APP_USER}" "$APP_ROOT"

echo "==> Python venv"
sudo -u "$APP_USER" python3 -m venv "$APP_ROOT/.venv"
sudo -u "$APP_USER" "$APP_ROOT/.venv/bin/pip" install -U pip
sudo -u "$APP_USER" "$APP_ROOT/.venv/bin/pip" install -r "$APP_ROOT/vps/requirements.txt"

echo "==> Firewall (port ${APP_PORT} for GitHub Actions)"
ufw allow OpenSSH
ufw allow "${APP_PORT}/tcp"
ufw --force enable

cat <<EOF

Next steps:
1. Place each film at: ${MOVIES_DIR}/{slug}/movie.mp4 + subtitles.srt
2. Match slug in config/movie_queue.json
3. Create ${APP_ROOT}/.env (see .env.example): WEBHOOK_SECRET, MOVIES_DIR, NICHE_PORT=${APP_PORT}
4. cp ${APP_ROOT}/deploy/retro-clip-worker.service /etc/systemd/system/
5. systemctl daemon-reload && systemctl enable --now retro-clip-worker
6. curl http://localhost:${APP_PORT}/health

Example:
  mkdir -p ${MOVIES_DIR}/inception-2010
  # upload movie.mp4 and subtitles.srt into that folder

EOF

echo "Done setup at ${APP_ROOT}"
