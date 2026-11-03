#!/usr/bin/env bash
# Restore a database dump produced by backup.sh (and optionally a media archive).
#
#   db/scripts/restore.sh db/backups/esabong-20261028T021500Z.dump [db/backups/media-20261028T021500Z.tar.gz]
#
# The API, worker and beat are stopped during the restore. The target database
# is dropped and recreated, then migrated to head in case the dump is older.
set -euo pipefail
cd "$(dirname "$0")/../.."

DUMP="${1:?usage: restore.sh <dump file> [media archive]}"
MEDIA="${2:-}"
[ -f "$DUMP" ] || { echo "Dump not found: $DUMP" >&2; exit 1; }
. db/scripts/lib-env.sh; load_env .env
DB_NAME="${DB_NAME:-esabong}"
SUPER="${POSTGRES_SUPERUSER:-postgres}"

TARGET="$DB_NAME"
if [ -n "${ESB_STACK:-}" ]; then
  # Contabo layout: restore only in the environment's own checkout, never the other
  # environment's dump (production data must not reach UAT, nor UAT data production).
  [ "$(basename "$(pwd)")" = "$ESB_STACK" ] || { echo "ESB_STACK=$ESB_STACK but this checkout is $(pwd)" >&2; exit 1; }
  case "$(basename "$DUMP")" in
    "${ESB_STACK}"-*) ;;
    prod-* | uat-*) echo "Refusing: $(basename "$DUMP") belongs to another environment than $ESB_STACK" >&2; exit 1 ;;
    *) echo "WARNING: $(basename "$DUMP") has no environment prefix; make sure it is a ${ESB_STACK} dump." ;;
  esac
  TARGET="${ESB_STACK}:${DB_NAME}"
fi
read -r -p "This REPLACES database '${TARGET}'. Type ${TARGET} to continue: " CONFIRM
[ "$CONFIRM" = "$TARGET" ] || { echo "Aborted."; exit 1; }

echo "==> Stopping application services"
docker compose stop api worker beat caddy

echo "==> Recreating database"
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 --username "$SUPER" --dbname postgres <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "${DB_NAME}";
CREATE DATABASE "${DB_NAME}" OWNER esabong_owner;
REVOKE ALL ON DATABASE "${DB_NAME}" FROM PUBLIC;
GRANT CONNECT ON DATABASE "${DB_NAME}" TO esabong_app, esabong_platform;
SQL
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 --username "$SUPER" --dbname "$DB_NAME" <<'SQL'
ALTER SCHEMA public OWNER TO esabong_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO esabong_app, esabong_platform;
SQL

echo "==> Restoring ${DUMP}"
NAME="$(basename "$DUMP")"
docker compose cp "$DUMP" "postgres:/tmp/${NAME}"
# Restored as the superuser: tables use FORCE ROW LEVEL SECURITY, so loading
# rows as the owner role would be rejected by the tenant policies.
docker compose exec -T postgres pg_restore --username "$SUPER" --dbname "$DB_NAME" \
  --no-owner --exit-on-error "/tmp/${NAME}"
docker compose exec -T postgres rm -f "/tmp/${NAME}"

echo "==> Returning ownership to esabong_owner"
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 --username "$SUPER" --dbname "$DB_NAME" <<'SQL'
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO esabong_owner', r.tablename);
  END LOOP;
  FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO esabong_owner', r.sequence_name);
  END LOOP;
  FOR r IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO esabong_owner', r.fn);
  END LOOP;
END $$;
SQL

echo "==> Applying any newer migrations"
docker compose run --rm migrate

if [ -n "$MEDIA" ]; then
  echo "==> Restoring media from ${MEDIA}"
  docker compose run --rm --no-deps -v "$(cd "$(dirname "$MEDIA")" && pwd):/in" --entrypoint sh api \
    -c "rm -rf /data/media/* && tar -xzf /in/$(basename "$MEDIA") -C /data"
fi

echo "==> Starting application services"
docker compose up -d api worker beat caddy
echo "Restore complete."
