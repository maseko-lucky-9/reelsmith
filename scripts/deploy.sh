#!/usr/bin/env bash
# Volume-safe local Docker deploy (W1.15 + Appendix A.11).
#
# Defaults are non-destructive:
#   - tar-snapshot the postgres volume to data/backups/ first
#   - docker compose up -d --no-recreate (NEVER -v)
#   - alembic upgrade head
#   - smoke /api/health
#
# Force a recreate (e.g. when a migration requires it) with:
#   RECREATE=1 scripts/deploy.sh
#
# Skip the snapshot (faster local iteration; not for waves) with:
#   SKIP_SNAPSHOT=1 scripts/deploy.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

POSTGRES_VOLUME="${POSTGRES_VOLUME:-reelsmith_postgres_data}"
BACKUP_DIR="${BACKUP_DIR:-$REPO/data/backups}"

if [ "${SKIP_SNAPSHOT:-0}" = "0" ]; then
  echo "==> Snapshotting Postgres volume → ${BACKUP_DIR}/"
  mkdir -p "$BACKUP_DIR"
  TS="$(date +%Y%m%d-%H%M%S)"
  if docker volume inspect "$POSTGRES_VOLUME" >/dev/null 2>&1; then
    docker run --rm \
      -v "${POSTGRES_VOLUME}:/data:ro" \
      -v "${BACKUP_DIR}:/backup" \
      busybox tar czf "/backup/postgres-${TS}.tgz" -C /data .
    echo "    saved postgres-${TS}.tgz"
  else
    echo "    (volume not yet created; skipping snapshot)"
  fi
fi

echo "==> docker compose up"
if [ "${RECREATE:-0}" = "1" ]; then
  docker compose up -d
else
  docker compose up -d --no-recreate
fi

echo "==> alembic upgrade head"
# Run alembic on the host (Python venv) — the API container also runs it on
# startup, but we run it here too so the deploy fails fast if migrations
# are broken.
alembic upgrade head

echo "==> smoke /api/health"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "    healthy"
    exit 0
  fi
  sleep 2
done

echo "    health check FAILED" >&2
docker compose ps
exit 1
