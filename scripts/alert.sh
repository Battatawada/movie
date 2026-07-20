#!/usr/bin/env bash
# Post message to Discord/Telegram-compatible webhook
set -euo pipefail
MSG="${1:-Pipeline notification}"
URL="${ALERT_WEBHOOK_URL:-}"
if [[ -z "$URL" ]]; then
  echo "ALERT_WEBHOOK_URL not set; skipping alert"
  exit 0
fi
curl -sS -X POST "$URL" \
  -H "Content-Type: application/json" \
  -d "{\"content\": $(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MSG")}"
