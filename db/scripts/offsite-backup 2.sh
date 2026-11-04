#!/usr/bin/env bash
# Nightly backup with an off-site copy that is proven recoverable.
#
#   db/scripts/offsite-backup.sh                      # backup + verify + upload + download-back check
#   OFFSITE_RESTORE_DRILL=1 db/scripts/offsite-backup.sh   # also restore the downloaded copy into a scratch DB
#
# Steps (any failure stops the run, exits non-zero and records the failure for monitoring):
#   1. db/scripts/backup.sh           — dump + checksum + media archive (owner-only files)
#   2. db/scripts/verify-backup.sh    — restore the new dump into a scratch DB and compare with live
#   3. upload dump, .sha256 and media archive to $OFFSITE_REMOTE with rclone (TLS)
#   4. download the uploaded dump into a temp dir and check it against the local .sha256
#   5. optional: verify-backup.sh on the downloaded copy (full recovery drill from off-site)
#   6. delete remote backups older than $OFFSITE_RETENTION_DAYS
#   7. write db/backups/.offsite-status (read by infrastructure/monitoring/check.sh)
#
# Configuration lives in a root-only env file (default: ./.offsite.env, mode 600, git-ignored),
# never in .env or on the command line, so credentials do not appear in `ps`, shell history
# or logs. rclone runs in its official container; nothing is installed on the host.
#
# Oracle Cloud Object Storage (S3-compatible API, "Customer Secret Key"):
#   OFFSITE_REMOTE=oci:esabong-backups/prod
#   RCLONE_CONFIG_OCI_TYPE=s3
#   RCLONE_CONFIG_OCI_PROVIDER=Other
#   RCLONE_CONFIG_OCI_ENDPOINT=https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
#   RCLONE_CONFIG_OCI_REGION=<region>
#   RCLONE_CONFIG_OCI_ACCESS_KEY_ID=<customer secret key id>
#   RCLONE_CONFIG_OCI_SECRET_ACCESS_KEY=<customer secret key>
#   RCLONE_CONFIG_OCI_NO_CHECK_BUCKET=true
#   OFFSITE_RETENTION_DAYS=35
# Use a private bucket in a different region from the server, a dedicated IAM user whose
# policy only allows object read/write in that bucket, and an Object Storage retention rule.
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV_FILE="${OFFSITE_ENV_FILE:-.offsite.env}"
STATUS="db/backups/.offsite-status"
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.71}"
umask 077

record() {  # record <ok|failed> <message>
  mkdir -p db/backups
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" > "$STATUS"
}
fail() { record failed "$1"; echo "OFFSITE BACKUP FAILED: $1" >&2; exit 1; }
trap 'record failed "unexpected error on line $LINENO"' ERR

[ -f "$ENV_FILE" ] || fail "missing $ENV_FILE (see header of this script for the required settings)"
perms="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE")"
case "$perms" in 600|400) ;; *) fail "$ENV_FILE must be mode 600 (is $perms)";; esac
OFFSITE_REMOTE="$(grep -E '^OFFSITE_REMOTE=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
RETENTION="$(grep -E '^OFFSITE_RETENTION_DAYS=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
RETENTION="${RETENTION:-35}"
[ -n "$OFFSITE_REMOTE" ] || fail "OFFSITE_REMOTE is not set in $ENV_FILE"
# Contabo layout: each environment uploads to (and prunes) only its own remote folder.
STACK="$(grep -E '^ESB_STACK=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
if [ -n "$STACK" ]; then
  case "${OFFSITE_REMOTE%/}" in
    */"$STACK") ;;
    *) fail "OFFSITE_REMOTE must end with /$STACK (one folder per environment; retention deletes old files in it)" ;;
  esac
fi
LOCAL_MOUNT=()
if [ -n "${OFFSITE_LOCAL_DIR:-}" ]; then  # drills only: a local-filesystem "remote"
  LOCAL_MOUNT=(-v "${OFFSITE_LOCAL_DIR}:/remote")
fi

rclone() {
  docker run --rm --env-file "$ENV_FILE" ${LOCAL_MOUNT[@]+"${LOCAL_MOUNT[@]}"} -v "$(pwd)/db/backups:/data" \
    --user "$(id -u):$(id -g)" "$RCLONE_IMAGE" --log-level NOTICE "$@"
}

echo "==> [1/6] Local backup"
out="$(db/scripts/backup.sh)"
echo "$out" | tail -n 2
DUMP="$(echo "$out" | sed -n 's/^Backup complete: //p')"
[ -f "$DUMP" ] || fail "backup.sh did not report a dump"
NAME="$(basename "$DUMP")"
STAMP="${NAME%.dump}"; STAMP="${STAMP##*-}"
MEDIA="db/backups/media-${STAMP}.tar.gz"

echo "==> [2/6] Restore drill on the new dump"
db/scripts/verify-backup.sh "$DUMP" | tail -n 1

echo "==> [3/6] Uploading to ${OFFSITE_REMOTE%%:*}:… (destination path not logged)"
files=("$NAME" "$NAME.sha256")
[ -f "$MEDIA" ] && files+=("$(basename "$MEDIA")")
for f in "${files[@]}"; do
  rclone copyto "/data/$f" "${OFFSITE_REMOTE}/$f" || fail "upload of $f failed"
done

echo "==> [4/6] Downloading the off-site copy back and checking its checksum"
CHECK="db/backups/.offsite-check"
rm -rf "$CHECK"; mkdir -p "$CHECK"
rclone copyto "${OFFSITE_REMOTE}/$NAME" "/data/.offsite-check/$NAME" || fail "download-back of $NAME failed"
cp "$DUMP.sha256" "$CHECK/"
(cd "$CHECK" && { shasum -a 256 -c "$NAME.sha256" 2>/dev/null || sha256sum -c "$NAME.sha256"; }) >/dev/null \
  || fail "off-site copy of $NAME does not match its checksum"
echo "    off-site copy matches ${NAME}.sha256"

if [ "${OFFSITE_RESTORE_DRILL:-0}" = "1" ]; then
  echo "==> [5/6] Restoring the downloaded off-site copy into a scratch database"
  db/scripts/verify-backup.sh "$CHECK/$NAME" | tail -n 1
else
  echo "==> [5/6] Off-site restore drill skipped (set OFFSITE_RESTORE_DRILL=1, e.g. weekly)"
fi
rm -rf "$CHECK"

echo "==> [6/6] Deleting off-site backups older than ${RETENTION} days"
rclone delete "${OFFSITE_REMOTE}" --min-age "${RETENTION}d" || fail "remote retention failed"

record ok "$NAME uploaded and verified$([ "${OFFSITE_RESTORE_DRILL:-0}" = "1" ] && echo ', restore drill passed')"
trap - ERR
echo "OFFSITE BACKUP OK: $NAME"
