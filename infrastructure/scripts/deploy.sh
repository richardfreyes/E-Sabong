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
# backup this script printed (see docs/deployment-contabo.md).
#
# Contabo layout (UAT + production on one VPS; ESB_STACK in .env): run it from the checkout of
# the environment you mean, /opt/esabong/uat or /opt/esabong/prod. It then also refuses to run
# when the directory, ESB_STACK, project name or compose files disagree; when a shell variable
# would override .env; when the compose config reaches outside this environment; when the
# sibling environment shares a secret or port; and the migration approval must name the stack:
#   APPROVED_MIGRATIONS=uat:<head>   (an approval for UAT can never migrate production)
set -euo pipefail
cd "$(dirname "$0")/../.."

die() { echo "deploy: $*" >&2; exit 1; }
REF="${1:?usage: infrastructure/scripts/deploy.sh <git tag or commit>}"
. db/scripts/lib-env.sh

[ -f .env ] || die ".env missing (copy .env.contabo.prod.example or .env.contabo.uat.example and fill it in)"
[ "$(stat -c %a .env 2>/dev/null || stat -f %Lp .env)" = "600" ] || die ".env must be mode 600 (chmod 600 .env)"
# docker compose lets exported shell variables override .env: refuse, so .env alone decides
# which project, database and secrets this run touches.
OVERRIDES="$(shell_overrides .env | tr '\n' ' ')"
[ -z "$OVERRIDES" ] || die "exported shell variables override .env: ${OVERRIDES}(unset them first)"
STACK="$(awk -F= '/^ESB_STACK=/ {v=$2} END {print v}' .env)"
if [ -n "$STACK" ]; then
  case "$STACK" in prod|uat) ;; *) die "ESB_STACK must be prod or uat, not '$STACK'" ;; esac
  [ "$(basename "$(pwd)")" = "$STACK" ] || die "ESB_STACK=$STACK but this checkout is $(pwd) (expected /opt/esabong/$STACK)"
  grep -Eq '^COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.contabo.yml$' .env \
    || die "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.contabo.yml must be set in .env"
  ! grep -Eq '^COMPOSE_PROJECT_NAME=' .env || die "remove COMPOSE_PROJECT_NAME from .env (the project is esabong-$STACK)"
  if [ "$STACK" = prod ]; then
    ! grep -Eiq '^(SYNTHETIC_DATA_ENABLED|STREAM_TEST_SOURCE)=("|\x27)?(1|true|yes|on)' .env \
      || die "production must not enable SYNTHETIC_DATA_ENABLED or STREAM_TEST_SOURCE"
  fi
  docker network inspect "esabong-$STACK-gateway" > /dev/null 2>&1 \
    || die "network esabong-$STACK-gateway missing: run infrastructure/scripts/gateway.sh networks"
  SIBLING="$([ "$STACK" = prod ] && echo uat || echo prod)"
  if [ -r "../$SIBLING/.env" ]; then
    echo "==> Isolation check against ../$SIBLING"
    ENVS=("../prod/.env" "../uat/.env"); [ "$STACK" = prod ] && ENVS=(".env" "../uat/.env") || ENVS=("../prod/.env" ".env")
    GW=(); [ -r ../prod/infrastructure/gateway/.env ] && GW=(--gateway-env ../prod/infrastructure/gateway/.env)
    python3 infrastructure/scripts/check-env-isolation.py envs "${ENVS[@]}" ${GW[@]+"${GW[@]}"} \
      || die "the two environments are not isolated (see FAIL lines)"
  else
    echo "    (../$SIBLING/.env not readable: cross-environment isolation check skipped)"
  fi
  LABEL="esabong-$STACK"
else
  grep -Eq '^COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml' .env \
    || die "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml must be set in .env (production overlay)"
  LABEL="esabong"
fi
HISTORY="${DEPLOY_HISTORY:-$HOME/$LABEL-deploy-history.log}"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "tracked files are modified in $(pwd); refusing to deploy over local changes"

PREV="$(git rev-parse --short HEAD)"
git fetch --tags --quiet origin
git checkout --quiet --detach "$REF"
NEW="$(git rev-parse --short HEAD)"
echo "==> Release $REF ($PREV -> $NEW)"

echo "==> Building images (running containers are not touched)"
docker compose build --pull

PROJECT="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
if [ -n "$STACK" ]; then
  [ "$PROJECT" = "esabong-$STACK" ] || die "compose project is $PROJECT, expected esabong-$STACK"
  docker compose config --format json | python3 infrastructure/scripts/check-env-isolation.py compose "$STACK" \
    || die "compose config reaches outside esabong-$STACK (see FAIL lines)"
fi
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
  APPROVAL="$HEAD"; [ -n "$STACK" ] && APPROVAL="$STACK:$HEAD"
  if [ "${APPROVED_MIGRATIONS:-}" != "$APPROVAL" ]; then
    echo
    echo "STOPPED: migrations up to $HEAD are pending in ${PROJECT} and not approved. Nothing was restarted."
    echo "After approval:  APPROVED_MIGRATIONS=$APPROVAL $0 $REF"
    echo "To abandon:      git checkout --detach $PREV"
    exit 3
  fi
  if [ -n "$CURRENT" ]; then
    echo "==> Backup before migrating (kept; not pruned)"
    BACKUP_OUT="$(mktemp "${TMPDIR:-/tmp}/$LABEL-backup.XXXXXX")"
    PRUNE_BACKUPS=0 db/scripts/backup.sh | tee "$BACKUP_OUT"
    DUMP="$(awk '/^Backup complete:/ {print $3}' "$BACKUP_OUT")"
    rm -f "$BACKUP_OUT"
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
echo "$(date -u +%FT%TZ) $PROJECT $PREV -> $NEW ($REF) migrations=${CURRENT:-none}->$HEAD" >> "$HISTORY"
echo
echo "Deployed $REF. Next: smoke test (docs/deployment-contabo.md, section 10)."
