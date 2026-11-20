#!/usr/bin/env bash
# Inject a failure into demo-service so you can watch CloudRescue detect and repair it.
#   scripts/chaos.sh unhealthy   /health returns 503
#   scripts/chaos.sh hang        /health stalls (timeout)
#   scripts/chaos.sh crash       the process exits
#   scripts/chaos.sh stop        the container is stopped (connection refused)
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-}"
[[ -f .env ]] || { echo "Run 'make init' first." >&2; exit 1; }
token="$(grep -E '^CHAOS_TOKEN=' .env | cut -d= -f2-)"
base="${DEMO_URL:-http://127.0.0.1:8000}"

case "$mode" in
  unhealthy|hang|crash)
    curl -fsS -X POST -H "X-Chaos-Token: ${token}" "${base}/fail?mode=${mode}"
    echo
    ;;
  stop)
    docker stop demo-service
    ;;
  *)
    echo "usage: $0 {unhealthy|hang|crash|stop}" >&2
    exit 2
    ;;
esac
echo "Injected '${mode}'. Watch it heal:  make logs   |   make incidents"
