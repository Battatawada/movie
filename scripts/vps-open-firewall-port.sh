#!/usr/bin/env bash
# Open Oracle VPS iptables for the clip worker port (persists in /etc/iptables/rules.v4).
set -euo pipefail

PORT="${1:-${APP_PORT:-${NICHE_PORT:-8766}}}"
RULES_FILE="/etc/iptables/rules.v4"

if ! iptables -C INPUT -p tcp -m tcp --dport "${PORT}" -j ACCEPT 2>/dev/null; then
  # Insert before the first REJECT rule (OCI images reject all other inbound traffic).
  REJECT_LINE=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
  if [[ -n "${REJECT_LINE}" ]]; then
    iptables -I INPUT "${REJECT_LINE}" -p tcp -m tcp --dport "${PORT}" -j ACCEPT
  else
    iptables -A INPUT -p tcp -m tcp --dport "${PORT}" -j ACCEPT
  fi
  echo "Opened iptables TCP ${PORT}"
else
  echo "iptables TCP ${PORT} already open"
fi

if [[ -f "${RULES_FILE}" ]] && ! grep -q "dport ${PORT}" "${RULES_FILE}"; then
  sed -i "/dport 8765/a -A INPUT -p tcp -m tcp --dport ${PORT} -j ACCEPT" "${RULES_FILE}" 2>/dev/null \
    || sed -i "/--dport 22 -j ACCEPT/a -A INPUT -p tcp -m tcp --dport ${PORT} -j ACCEPT" "${RULES_FILE}"
  echo "Persisted ${PORT} in ${RULES_FILE}"
fi
