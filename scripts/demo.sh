#!/usr/bin/env bash
# End-to-end demo: break the service, wait for CloudRescue to repair it, show the incident.
set -euo pipefail
cd "$(dirname "$0")/.."

base="${DEMO_URL:-http://127.0.0.1:8000}"
mode="${1:-unhealthy}"
timeout_s="${DEMO_TIMEOUT:-120}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1. Service is healthy"
curl -fsS "${base}/health"; echo

say "2. Injecting failure: ${mode}"
scripts/chaos.sh "${mode}" >/dev/null || true
sleep 1
curl -sS -o /dev/null -w "   /health now answers HTTP %{http_code}\n" --max-time 3 "${base}/health" || echo "   /health unreachable"

say "3. Waiting for automatic recovery (max ${timeout_s}s)"
start=$(date +%s)
until curl -fsS --max-time 2 "${base}/health" >/dev/null 2>&1; do
  if (( $(date +%s) - start > timeout_s )); then
    echo "   NOT recovered within ${timeout_s}s. Check: make logs" >&2
    exit 1
  fi
  sleep 2
done
echo "   recovered after $(( $(date +%s) - start ))s"

say "4. Incident record"
docker compose exec -T monitor python cli.py incidents --limit 3
