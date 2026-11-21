#!/usr/bin/env bash
# Nightly backup with an off-site copy that is proven recoverable.
#
#   db/scripts/offsite-backup.sh                      # backup + verify + upload + download-back check
#   OFFSITE_RESTORE_DRILL=1 db/scripts/offsite-backup.sh   # also restore the downloaded copy into a scratch DB
#   ESB_ROOT=/opt/esabong/prod OFFSITE_ENV_FILE=~/esb-backup/prod.offsite.env ~/esb-backup/offsite-backup.sh
#                                                     # scheduled copy outside the release checkouts
#
# Encryption: with OFFSITE_PASSPHRASE_FILE set in the env file (a file of its own, mode 600, also
# kept in the owner's password manager) every uploaded file is encrypted on the server with GnuPG
# (AES-256, symmetric) before it leaves; the store only ever holds *.gpg. The download-back check
# decrypts the copy and compares it with the dump's .sha256, so a backup is reported OK only when
# it can be decrypted. Restore elsewhere: gpg --batch --pinentry-mode loopback
#   --passphrase-file <file> -d -o x.dump x.dump.gpg   (then db/scripts/restore.sh).
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
#
# Cloudflare R2 (API token with "Object Read & Write" on the backup bucket only):
#   OFFSITE_REMOTE=r2:esabong-backups/prod
#   RCLONE_CONFIG_R2_TYPE=s3
#   RCLONE_CONFIG_R2_PROVIDER=Cloudflare
#   RCLONE_CONFIG_R2_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
#   RCLONE_CONFIG_R2_ACCESS_KEY_ID=<token access key id>
#   RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=<token secret>
#   RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
#   OFFSITE_PASSPHRASE_FILE=/home/deploy/esb-backup/backup.passphrase
#   OFFSITE_RETENTION_DAYS=35
# Write secrets with infrastructure/scripts/put-secret.sh (never typed on a command line).
set -euo pipefail
ESB_ROOT="${ESB_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ESB_ROOT"
# The restore check this script runs; beside the script when installed outside a checkout (it
# needs SAVE_FINGERPRINT/COMPARE_WITH, which older checkouts' verify-backup.sh lack).
VERIFY="db/scripts/verify-backup.sh"
[ -x "$(dirname "$0")/verify-backup.sh" ] && VERIFY="$(cd "$(dirname "$0")" && pwd)/verify-backup.sh"

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
PASSFILE="$(grep -E '^OFFSITE_PASSPHRASE_FILE=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
[ -n "$OFFSITE_REMOTE" ] || fail "OFFSITE_REMOTE is not set in $ENV_FILE"
if [ -n "$PASSFILE" ]; then
  [ -s "$PASSFILE" ] || fail "OFFSITE_PASSPHRASE_FILE does not exist or is empty"
  pperms="$(stat -c %a "$PASSFILE" 2>/dev/null || stat -f %Lp "$PASSFILE")"
  case "$pperms" in 600|400) ;; *) fail "the passphrase file must be mode 600 (is $pperms)";; esac
  command -v gpg >/dev/null || fail "gpg is required for encrypted backups"
  GNUPGHOME="$(mktemp -d)"; export GNUPGHOME   # throwaway keyring: nothing persists
fi
gpg_enc() {  # gpg_enc <in> <out>
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$PASSFILE" --symmetric \
    --cipher-algo AES256 --s2k-digest-algo SHA512 --s2k-count 65011712 --compress-algo none -o "$2" "$1"
}
gpg_dec() {  # gpg_dec <in> <out>
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$PASSFILE" -d -o "$2" "$1"
}
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
FPRINT="db/backups/.${NAME}.fingerprint"   # restored state of this dump, for the off-site drill
SAVE_FINGERPRINT="$FPRINT" "$VERIFY" "$DUMP" | tail -n 1

sha() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | awk '{print $1}'; }
UP="$NAME"   # the object that is uploaded and checked back
if [ -n "$PASSFILE" ]; then
  echo "==> [3/6] Encrypting (AES-256) and uploading to ${OFFSITE_REMOTE%%:*}:… (destination path not logged)"
  gpg_enc "$DUMP" "$DUMP.gpg" || fail "encryption of $NAME failed"
  UP="$NAME.gpg"
  files=("$UP" "$NAME.sha256")
  if [ -f "$MEDIA" ]; then gpg_enc "$MEDIA" "$MEDIA.gpg" || fail "encryption of the media archive failed"; files+=("$(basename "$MEDIA").gpg"); fi
else
  echo "==> [3/6] Uploading UNENCRYPTED to ${OFFSITE_REMOTE%%:*}:… (set OFFSITE_PASSPHRASE_FILE to encrypt)"
  files=("$NAME" "$NAME.sha256")
  [ -f "$MEDIA" ] && files+=("$(basename "$MEDIA")")
fi
for f in "${files[@]}"; do
  rclone copyto "/data/$f" "${OFFSITE_REMOTE}/$f" || fail "upload of $f failed"
done

echo "==> [4/6] Downloading the off-site copy back and checking it"
CHECK="db/backups/.offsite-check"
rm -rf "$CHECK"; mkdir -p "$CHECK"
rclone copyto "${OFFSITE_REMOTE}/$UP" "/data/.offsite-check/$UP" || fail "download-back of $UP failed"
if [ -n "$PASSFILE" ]; then
  [ "$(sha "$CHECK/$UP")" = "$(sha "$DUMP.gpg")" ] || fail "off-site copy of $UP differs from what was uploaded"
  gpg_dec "$CHECK/$UP" "$CHECK/$NAME" || fail "off-site copy of $UP cannot be decrypted"
  rm -f "$CHECK/$UP" "$DUMP.gpg" "${MEDIA}.gpg"
fi
[ "$(sha "$CHECK/$NAME")" = "$(awk '{print $1}' "$DUMP.sha256")" ] || fail "off-site copy of $NAME does not match its checksum"
echo "    off-site copy $([ -n "$PASSFILE" ] && echo 'decrypts and ')matches ${NAME}.sha256"

if [ "${OFFSITE_RESTORE_DRILL:-0}" = "1" ]; then
  echo "==> [5/6] Restoring the downloaded off-site copy into a scratch database"
  # Compared with the restore of the original dump, not with live data that has moved on since.
  COMPARE_WITH="$FPRINT" "$VERIFY" "$CHECK/$NAME" | tail -n 1
else
  echo "==> [5/6] Off-site restore drill skipped (set OFFSITE_RESTORE_DRILL=1, e.g. weekly)"
fi
rm -rf "$CHECK" "$FPRINT"
[ -n "$PASSFILE" ] && rm -rf "$GNUPGHOME"

echo "==> [6/6] Deleting off-site backups older than ${RETENTION} days"
rclone delete "${OFFSITE_REMOTE}" --min-age "${RETENTION}d" || fail "remote retention failed"

record ok "$NAME uploaded and verified$([ "${OFFSITE_RESTORE_DRILL:-0}" = "1" ] && echo ', restore drill passed')"
trap - ERR
echo "OFFSITE BACKUP OK: $NAME"
