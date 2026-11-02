#!/usr/bin/env bash
# Restore drill that never touches the live database.
#
#   db/scripts/verify-backup.sh db/backups/esabong-20261028T021500Z.dump
#
# Loads the dump into a scratch database (<DB_NAME>_restore_check), then compares it with
# the live database: every table's row count, row-level-security policies, FORCE RLS flags,
# grants for the application roles, triggers and the Alembic revision. Drops the scratch
# database afterwards. Exit code 0 means the dump restores to an equivalent database.
#
# Row counts are compared exactly; run it right after backup.sh (or with the app stopped)
# so normal traffic does not create differences. Use KEEP=1 to keep the scratch database.
set -euo pipefail
cd "$(dirname "$0")/../.."

DUMP="${1:?usage: verify-backup.sh <dump file>}"
[ -f "$DUMP" ] || { echo "Dump not found: $DUMP" >&2; exit 1; }
. db/scripts/lib-env.sh; load_env .env
DB_NAME="${DB_NAME:-esabong}"
SCRATCH="${DB_NAME}_restore_check"
SUPER="${POSTGRES_SUPERUSER:-postgres}"
NAME="$(basename "$DUMP")"

if [ -f "${DUMP}.sha256" ]; then
  echo "==> Checking ${NAME}.sha256"
  (cd "$(dirname "$DUMP")" && shasum -a 256 -c "${NAME}.sha256" 2>/dev/null || sha256sum -c "${NAME}.sha256")
fi

psql_super() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -X -q --username "$SUPER" "$@"; }

cleanup() {
  docker compose exec -T postgres rm -f "/tmp/${NAME}" >/dev/null 2>&1 || true
  if [ "${KEEP:-0}" != "1" ]; then
    psql_super --dbname postgres -c "DROP DATABASE IF EXISTS \"${SCRATCH}\"" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "==> Creating scratch database ${SCRATCH}"
psql_super --dbname postgres <<SQL
DROP DATABASE IF EXISTS "${SCRATCH}";
CREATE DATABASE "${SCRATCH}" OWNER esabong_owner;
REVOKE ALL ON DATABASE "${SCRATCH}" FROM PUBLIC;
SQL
psql_super --dbname "$SCRATCH" -c "ALTER SCHEMA public OWNER TO esabong_owner; REVOKE CREATE ON SCHEMA public FROM PUBLIC;"

echo "==> Restoring ${NAME} into ${SCRATCH}"
docker compose cp "$DUMP" "postgres:/tmp/${NAME}"
docker compose exec -T postgres pg_restore --username "$SUPER" --dbname "$SCRATCH" --no-owner --exit-on-error "/tmp/${NAME}"

# One line per fact, sorted, so the two databases can be diffed directly.
FINGERPRINT_SQL=$(cat <<'SQL'
\pset format unaligned
\pset tuples_only on
SELECT 'revision ' || version_num FROM alembic_version;
SELECT 'policy ' || tablename || ' ' || policyname || ' ' || cmd FROM pg_policies WHERE schemaname = 'public';
SELECT 'rls ' || relname || ' ' || relrowsecurity || ' ' || relforcerowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r';
SELECT 'grant ' || table_name || ' ' || grantee || ' ' || string_agg(privilege_type, ',' ORDER BY privilege_type)
  FROM information_schema.role_table_grants
  WHERE table_schema = 'public' AND grantee IN ('esabong_app', 'esabong_platform') GROUP BY table_name, grantee;
SELECT 'trigger ' || event_object_table || ' ' || trigger_name || ' ' || event_manipulation
  FROM information_schema.triggers WHERE trigger_schema = 'public';
SELECT 'rows ' || r.relname || ' ' ||
       (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM public.%I', r.relname), false, true, '')))[1]::text
  FROM pg_class r JOIN pg_namespace n ON n.oid = r.relnamespace WHERE n.nspname = 'public' AND r.relkind = 'r';
SQL
)

TMP="$(mktemp -d)"
echo "==> Fingerprinting ${DB_NAME} and ${SCRATCH}"
psql_super --dbname "$DB_NAME" <<<"$FINGERPRINT_SQL" | sort > "$TMP/live.txt"
psql_super --dbname "$SCRATCH" <<<"$FINGERPRINT_SQL" | sort > "$TMP/restored.txt"

tables=$(grep -c '^rows ' "$TMP/restored.txt" || true)
rows=$(awk '/^rows /{s+=$3} END{print s+0}' "$TMP/restored.txt")
policies=$(grep -c '^policy ' "$TMP/restored.txt" || true)
triggers=$(grep -c '^trigger ' "$TMP/restored.txt" || true)
echo "    restored: ${tables} tables, ${rows} rows, ${policies} RLS policies, ${triggers} triggers, $(grep '^revision ' "$TMP/restored.txt")"

if diff -u "$TMP/live.txt" "$TMP/restored.txt" > "$TMP/diff.txt"; then
  echo "RESTORE VERIFIED: ${NAME} matches ${DB_NAME} (row counts, policies, RLS flags, grants, triggers, revision)"
  rm -rf "$TMP"
else
  echo "RESTORE MISMATCH (- live, + restored):" >&2
  grep -E '^[-+][a-z]' "$TMP/diff.txt" | head -n 50 >&2
  echo "Full fingerprints kept in $TMP" >&2
  exit 1
fi
