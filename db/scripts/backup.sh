#!/usr/bin/env bash
# Online backup of the database (pg_dump custom format) and the media volume.
#
#   db/scripts/backup.sh                 # writes to db/backups/
#   RETENTION_DAYS=30 db/scripts/backup.sh
#
# Schedule with cron on the host, e.g.: 15 2 * * * cd /opt/esabong && db/scripts/backup.sh
# Copy db/backups/ off the host (object storage, another region): a backup on
# the same disk is not a backup.
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${RETENTION_DAYS:=14}"
. db/scripts/lib-env.sh; load_env .env
DB_NAME="${DB_NAME:-esabong}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="db/backups"
# Backups hold every tenant's data: owner-only directory and files.
umask 077
mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"
# Contabo layout: the environment is part of the name (uat-esabong-<stamp>.dump), so a dump can
# never be mistaken for the other environment's (restore.sh refuses cross-environment dumps).
BASE="${DB_NAME}-${STAMP}"; [ -n "${ESB_STACK:-}" ] && BASE="${ESB_STACK}-${BASE}"
DUMP="${OUT_DIR}/${BASE}.dump"

echo "==> Dumping database ${DB_NAME}"
# Superuser connection: bypasses RLS so every tenant's rows are included.
# Grants are kept (restore relies on them); ownership is reassigned on restore.
# Streamed to a host file created under umask 077 (mode 600), not written by the container,
# so the dump is never world-readable whatever the container's umask or bind-mount semantics.
trap 'rm -f "$DUMP.partial"' EXIT
docker compose exec -T postgres pg_dump \
  --username "${POSTGRES_SUPERUSER:-postgres}" --dbname "$DB_NAME" \
  --format custom --compress 9 --no-owner > "$DUMP.partial"
mv "$DUMP.partial" "$DUMP"

echo "==> Verifying archive"
docker compose exec -T postgres pg_restore --list < "$DUMP" > /dev/null
# Checksum next to the dump, so a copy fetched back from off-site storage can be checked
# before it is restored (verify-backup.sh checks it automatically).
(cd "$OUT_DIR" && { shasum -a 256 "${BASE}.dump" 2>/dev/null || sha256sum "${BASE}.dump"; } \
  > "${BASE}.dump.sha256")

echo "==> Archiving media volume"
docker compose run --rm --no-deps -v "$(pwd)/${OUT_DIR}:/out" --entrypoint sh api \
  -c "umask 077; tar -czf /out/media-${STAMP}.tar.gz -C /data media" 2>/dev/null \
  || echo "    (media volume not present, skipped)"
[ -f "${OUT_DIR}/media-${STAMP}.tar.gz" ] && chmod 600 "${OUT_DIR}/media-${STAMP}.tar.gz" 2>/dev/null || true

if [ "${PRUNE_BACKUPS:-1}" = "1" ]; then
  echo "==> Pruning backups older than ${RETENTION_DAYS} days"
  find "$OUT_DIR" -type f \( -name '*.dump' -o -name '*.dump.sha256' -o -name 'media-*.tar.gz' \) -mtime "+${RETENTION_DAYS}" -print -delete
fi

ls -lh "$OUT_DIR" | tail -n 5
echo "Backup complete: ${DUMP}"
echo "Verify it with:  db/scripts/verify-backup.sh ${DUMP}"
