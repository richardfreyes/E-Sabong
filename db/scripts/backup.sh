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
set -a; [ -f .env ] && . ./.env; set +a
DB_NAME="${DB_NAME:-esabong}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="db/backups"
mkdir -p "$OUT_DIR"

echo "==> Dumping database ${DB_NAME}"
# Superuser connection: bypasses RLS so every tenant's rows are included.
# Grants are kept (restore relies on them); ownership is reassigned on restore.
docker compose exec -T postgres pg_dump \
  --username "${POSTGRES_SUPERUSER:-postgres}" --dbname "$DB_NAME" \
  --format custom --compress 9 --no-owner \
  --file "/backups/${DB_NAME}-${STAMP}.dump"

echo "==> Verifying archive"
docker compose exec -T postgres pg_restore --list "/backups/${DB_NAME}-${STAMP}.dump" > /dev/null

echo "==> Archiving media volume"
docker compose run --rm --no-deps -v "$(pwd)/${OUT_DIR}:/out" --entrypoint sh api \
  -c "tar -czf /out/media-${STAMP}.tar.gz -C /data media" 2>/dev/null \
  || echo "    (media volume not present, skipped)"

echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "$OUT_DIR" -type f \( -name '*.dump' -o -name 'media-*.tar.gz' \) -mtime "+${RETENTION_DAYS}" -print -delete

ls -lh "$OUT_DIR" | tail -n 5
echo "Backup complete: ${OUT_DIR}/${DB_NAME}-${STAMP}.dump"
