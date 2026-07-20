#!/usr/bin/env bash
set -euo pipefail
PORT="${NICHE_PORT:-8766}"
echo "==> retro-clip-worker health"
curl -sf "http://127.0.0.1:${PORT}/health" && echo || echo "FAIL: worker not on :${PORT}"
echo "==> movies dir"
ls -la "${MOVIES_DIR:-/opt/movies}" 2>/dev/null || echo "No movies yet"
echo "==> disk"
df -h /opt / 2>/dev/null | tail -n +1
