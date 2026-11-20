#!/usr/bin/env bash
# Cron entry point for encrypted off-site backups of one environment (Contabo layout).
#
#   ~/esb-backup/offsite-scheduled.sh prod     # nightly; full restore drill of the off-site copy on Sundays
#   ~/esb-backup/offsite-scheduled.sh uat
#
# Runs offsite-backup.sh (kept next to this file, outside the release checkouts so deploys never
# change it mid-run) against /opt/esabong/<env> with ~/esb-backup/<env>.offsite.env. One run at a
# time per host (flock): a slow restore drill never overlaps the next environment's backup.
# Result: /opt/esabong/<env>/db/backups/.offsite-status (read by infrastructure/monitoring/check.sh).
set -euo pipefail
env="${1:?usage: offsite-scheduled.sh prod|uat}"
case "$env" in prod|uat) ;; *) echo "unknown environment: $env" >&2; exit 2 ;; esac
here="$(cd "$(dirname "$0")" && pwd)"
export ESB_ROOT="/opt/esabong/$env"
export OFFSITE_ENV_FILE="$here/$env.offsite.env"
[ "$(date +%u)" = 7 ] && export OFFSITE_RESTORE_DRILL=1
exec flock -w 7200 "$here/.offsite.lock" "$here/offsite-backup.sh"
