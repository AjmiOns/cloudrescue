#!/usr/bin/env bash
# Create .env with strong random secrets. Refuses to overwrite an existing file.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -e .env ]]; then
  echo ".env already exists; leaving it untouched." >&2
  exit 0
fi

rand() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
docker_gid="$(getent group docker | cut -d: -f3 || true)"
if [[ -z "$docker_gid" ]]; then
  echo "warning: no 'docker' group found; set DOCKER_GID in .env manually." >&2
  docker_gid=999
fi

umask 077
sed \
  -e "s|^CHAOS_TOKEN=.*|CHAOS_TOKEN=$(rand)|" \
  -e "s|^GUARD_TOKEN=.*|GUARD_TOKEN=$(rand)|" \
  -e "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=$(rand)|" \
  -e "s|^DOCKER_GID=.*|DOCKER_GID=${docker_gid}|" \
  .env.example > .env
echo "Created .env (mode 600). Grafana password: grep GRAFANA_ADMIN_PASSWORD .env"
