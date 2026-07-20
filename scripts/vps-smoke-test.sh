#!/usr/bin/env bash
set -euo pipefail
PORT="${NICHE_PORT:-8766}"
curl -sf "http://127.0.0.1:${PORT}/health"
