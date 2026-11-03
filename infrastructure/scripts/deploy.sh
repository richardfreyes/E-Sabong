#!/usr/bin/env bash
# Release deploy with a migration gate. Run on the server, in /opt/esabong, as the deploy user.
#
#   infrastructure/scripts/deploy.sh <git tag or commit>
#       Checks out the release, builds images and runs preflight. If the new release has
#       database migrations that are not applied yet, it STOPS here: images are built,
#       nothing is restarted, nothing is migrated. It prints the pending revisions.
#
#   APPROVED_MIGRATIONS=<head revision> infrastructure/scripts/deploy.sh <ref>
#       After the migration was approved: backs up and verifies the database, then starts the
#       release (the `migrate` service applies the migrations before the API starts).
#
# Why the gate: in this stack any `docker compose up -d` runs `migrate` first, so starting a
# release IS migrating. Never run a bare `docker compose up -d` after checking out a release
# with pending migrations unless the migration was approved.
#
# Rollback (application only, no migration in between): run this script with the previous
# tag. A release whose migration already ran is rolled back with db/scripts/restore.sh and the
# backup this script printed (see docs/deployment-contabo.md, section 12).
set -euo pipefail
cd "$(dirname "$0")/../.."

die() { echo "deploy: $*" >&2; exit 1; }
REF="${1:?usage: infrastructure/scripts/deploy.sh <git tag or commit>}"
HISTORY="${DEPLOY_HISTORY:-$HOME/esabong-deploy-history.log}"

[ -f .env ] || die ".env missing (copy .env.contabo.example and fill it in)"
[ "$(stat -c %a .env 2>/dev/null || stat -f %Lp .env)" = "600" ] || die ".env must be mode 600 (chmod 600 .env)"
grep -Eq '^COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml' .env \
  || die "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml must be set in .env (production overlay)"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "tracked files are modified in $(pwd); refusing to deploy over local changes"

PREV="$(git rev-parse --short HEAD)"
git fetch --tags --quiet origin
git checkout --quiet --detach "$REF"
NEW="$(git rev-parse --short HEAD)"
echo "==> Release $REF ($PREV -> $NEW)"

echo "==> Building images (running containers are not touched)"
docker compose build --pull

PROJECT="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
echo "==> Preflight (project $PROJECT)"
python3 infrastructure/scripts/preflight.py --api-image "${PROJECT}-api" || die "preflight failed; fix it or roll back: $0 $PREV"

echo "==> Checking for pending database migrations"
docker compose up -d --wait postgres redis
revision() { docker compose run --rm --no-deps -T migrate alembic "$1" 2>/dev/null | awk '/^[0-9]{4}/ {print $1}' | tail -n 1; }
CURRENT="$(revision current)"
HEAD="$(revision heads)"
[ -n "$HEAD" ] || die "could not read the migration head from the new image"
echo "    database: ${CURRENT:-<empty>}   release: $HEAD"

if [ "$CURRENT" != "$HEAD" ]; then
  echo "    pending migrations:"
  docker compose run --rm --no-deps -T migrate alembic history -r "${CURRENT:-base}:$HEAD" 2>/dev/null | sed 's/^/      /'
  if [ "${APPROVED_MIGRATIONS:-}" != "$HEAD" ]; then
    echo
    echo "STOPPED: migrations up to $HEAD are pending and not approved. Nothing was restarted."
    echo "After approval:  APPROVED_MIGRATIONS=$HEAD $0 $REF"
    echo "To abandon:      git checkout --detach $PREV"
    exit 3
  fi
  if [ -n "$CURRENT" ]; then
    echo "==> Backup before migrating (kept; not pruned)"
    PRUNE_BACKUPS=0 db/scripts/backup.sh | tee /tmp/esabong-backup.out
    DUMP="$(awk '/^Backup complete:/ {print $3}' /tmp/esabong-backup.out)"
    [ -n "$DUMP" ] || die "backup did not report a dump file"
    SCRATCH_DB_NAME="${DB_NAME:-esabong}_restore_$(date -u +%Y%m%d%H%M%S)" db/scripts/verify-backup.sh "$DUMP" \
      || die "backup verification failed; nothing was migrated"
    echo "    restore point: $DUMP"
  else
    echo "    empty database (first install): no backup needed"
  fi
fi

echo "==> Starting release"
docker compose up -d --remove-orphans
for _ in $(seq 1 60); do
  STATE="$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || echo starting)"
  [ "$STATE" = "healthy" ] && break
  sleep 5
done
[ "$STATE" = "healthy" ] || die "api is not healthy after 5 minutes (docker compose logs api); roll back: $0 $PREV"
MIGRATE_EXIT="$(docker inspect --format '{{.State.ExitCode}}' "$(docker compose ps -aq migrate)" 2>/dev/null || echo '?')"
[ "$MIGRATE_EXIT" = "0" ] || die "migrate exited with $MIGRATE_EXIT (docker compose logs migrate)"
docker compose ps
echo "$(date -u +%FT%TZ) $PREV -> $NEW ($REF) migrations=${CURRENT:-none}->$HEAD" >> "$HISTORY"
echo
echo "Deployed $REF. Next: smoke test (docs/deployment-contabo.md, section 10)."
