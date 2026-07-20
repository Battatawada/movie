#!/usr/bin/env bash
set -euo pipefail
RUN_ID="${1:?usage: vps-run-status.sh RUN_ID}"
PORT="${NICHE_PORT:-8766}"
SECRET="${WEBHOOK_SECRET:?set WEBHOOK_SECRET}"
curl -sf -H "Authorization: Bearer ${SECRET}" "http://127.0.0.1:${PORT}/runs/${RUN_ID}/status" | python3 -m json.tool
