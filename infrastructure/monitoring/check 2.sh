#!/usr/bin/env bash
# Host-side health and alert check. Run every 5 minutes (systemd timer or cron, see README.md).
#
#   infrastructure/monitoring/check.sh            # exit 0 = healthy, 1 = at least one alert
#   MONITOR_DRY_RUN=1 infrastructure/monitoring/check.sh   # print, never send
#
# Checks: API liveness and readiness (PostgreSQL + Redis), the three portals over HTTPS, the
# WebSocket upgrade, container state and health (incl. the Celery worker), CPU/memory per
# container, host disk, database size, HTTP 5xx rate at the edge, WebSocket task errors, backup
# freshness and off-site status, and TLS certificate expiry.
#
# Alerts go to every configured channel; nothing is configured by default:
#   ALERT_WEBHOOK_URL     Slack/Discord/Teams/Google Chat-compatible incoming webhook ({"text": ...})
#   OCI_ONS_TOPIC_ID      Oracle Cloud Notifications topic (email/SMS/PagerDuty subscriptions), via the OCI CLI
#   HEARTBEAT_URL         dead-man's switch pinged on every healthy run (e.g. healthchecks.io), so a
#                         dead host or a stopped timer also alerts
# Put these in ./.monitor.env (mode 600, git-ignored). Thresholds below can be overridden there.
# A problem alerts when it first appears, again every ALERT_REPEAT_MINUTES while it lasts, and
# once more when it clears. Messages contain no secrets: hosts, counts and statuses only.
set -uo pipefail
cd "$(dirname "$0")/../.."

. db/scripts/lib-env.sh
load_env .monitor.env
load_env .env

: "${PLATFORM_HOST:?PLATFORM_HOST is not set}"
MONITOR_TENANT_HOST="${MONITOR_TENANT_HOST:-}"     # e.g. demo.platform.example.ph (one real tenant)
DISK_ALERT_PCT="${DISK_ALERT_PCT:-85}"
MEM_ALERT_PCT="${MEM_ALERT_PCT:-90}"
CPU_ALERT_PCT="${CPU_ALERT_PCT:-90}"               # per container, % of one core x cores
DB_SIZE_ALERT_GB="${DB_SIZE_ALERT_GB:-40}"
ERROR_RATE_PCT="${ERROR_RATE_PCT:-2}"              # 5xx share of requests in the window
ERROR_MIN_REQUESTS="${ERROR_MIN_REQUESTS:-20}"     # ignore the rate below this volume
WS_ERRORS_MAX="${WS_ERRORS_MAX:-10}"
WINDOW="${MONITOR_WINDOW:-5m}"
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
TLS_ALERT_DAYS="${TLS_ALERT_DAYS:-14}"
ALERT_REPEAT_MINUTES="${ALERT_REPEAT_MINUTES:-60}"
STATE_DIR="${MONITOR_STATE_DIR:-/var/lib/esabong-monitor}"
CURL_TLS=(); [ "${MONITOR_INSECURE_TLS:-0}" = "1" ] && CURL_TLS=(-k)   # local CA drills only
RESOLVE=(); for r in ${MONITOR_RESOLVE:-}; do RESOLVE+=(--resolve "$r"); done  # staging without DNS: "host:port:addr ..."
HTTPS_PORT_SUFFIX=""; [ -n "${MONITOR_HTTPS_PORT:-}" ] && HTTPS_PORT_SUFFIX=":${MONITOR_HTTPS_PORT}"
mkdir -p "$STATE_DIR" 2>/dev/null || { STATE_DIR="${TMPDIR:-/tmp}/esabong-monitor"; mkdir -p "$STATE_DIR"; }

problems=()
notes=()
problem() { problems+=("$1"); }
note() { notes+=("$1"); }

http_code() {  # http_code <url> [extra curl args...]
  curl -s -o /dev/null -w '%{http_code}' --max-time 10 ${CURL_TLS[@]+"${CURL_TLS[@]}"} ${RESOLVE[@]+"${RESOLVE[@]}"} "$@" 2>/dev/null || true
}

# --- Edge, API and portals ---------------------------------------------------------------
base="https://${PLATFORM_HOST}${HTTPS_PORT_SUFFIX}"
[ "$(http_code "$base/api/health")" = "200" ] || problem "API liveness failed: $base/api/health"
ready="$(curl -s --max-time 10 ${CURL_TLS[@]+"${CURL_TLS[@]}"} ${RESOLVE[@]+"${RESOLVE[@]}"} "$base/api/ready" 2>/dev/null || true)"
case "$ready" in
  *'"database":"ok"'*'"redis":"ok"'*) ;;
  "") problem "API readiness unreachable: $base/api/ready" ;;
  *) problem "API readiness degraded: $(echo "$ready" | tr -d '\n' | cut -c1-200)" ;;
esac
[ "$(http_code "$base/")" = "200" ] || problem "Owner portal not serving: $base/"
if [ -n "$MONITOR_TENANT_HOST" ]; then
  tbase="https://${MONITOR_TENANT_HOST}${HTTPS_PORT_SUFFIX}"
  [ "$(http_code "$tbase/")" = "200" ] || problem "Player portal not serving: $tbase/"
  [ "$(http_code "$tbase/admin/")" = "200" ] || problem "Admin portal not serving: $tbase/admin/"
  # A WebSocket upgrade must reach the API (101), even though it then closes for lack of a token.
  ws="$(http_code "$tbase/api/ws" --http1.1 --max-time 4 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
        -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: ZXNhYm9uZy1tb25pdG9yIQ==')"
  [ "$ws" = "101" ] || problem "WebSocket upgrade failed at the edge (HTTP ${ws:-none})"
else
  note "MONITOR_TENANT_HOST not set: player/admin portal and WebSocket checks skipped"
fi

# --- TLS certificate expiry --------------------------------------------------------------
if [ "${MONITOR_INSECURE_TLS:-0}" != "1" ]; then
  for host in "$PLATFORM_HOST" ${MONITOR_TENANT_HOST:+"$MONITOR_TENANT_HOST"}; do
    cert="$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null | openssl x509 2>/dev/null || true)"
    if [ -z "$cert" ]; then
      problem "TLS certificate unreadable for $host"
    elif ! echo "$cert" | openssl x509 -noout -checkend $((TLS_ALERT_DAYS * 86400)) >/dev/null; then
      problem "TLS certificate for $host expires within ${TLS_ALERT_DAYS} days ($(echo "$cert" | openssl x509 -noout -enddate | cut -d= -f2))"
    fi
  done
fi

# --- Containers --------------------------------------------------------------------------
if ps_out="$(docker compose ps -a --format '{{.Service}} {{.State}} {{.Health}} {{.ExitCode}}' 2>/dev/null)"; then
  # MONITOR_EXTRA_SERVICES: e.g. "mediamtx" once public streaming is enabled.
  for svc in postgres redis api worker beat caddy ${MONITOR_EXTRA_SERVICES:-}; do
    line="$(echo "$ps_out" | awk -v s="$svc" '$1 == s' | head -n1)"
    state="$(echo "$line" | awk '{print $2}')"; health="$(echo "$line" | awk '{print $3}')"
    if [ -z "$line" ]; then problem "Container $svc is missing"
    elif [ "$state" != "running" ]; then problem "Container $svc is $state"
    elif [ "$health" = "unhealthy" ]; then problem "Container $svc is unhealthy"
    fi
  done
  migrate="$(echo "$ps_out" | awk '$1 == "migrate"' | head -n1)"
  if [ -n "$migrate" ] && [ "$(echo "$migrate" | awk '{print $NF}')" != "0" ]; then problem "Last migration run failed"; fi
else
  problem "docker compose ps failed (is Docker running?)"
fi

# --- Restarts (crash loops) --------------------------------------------------------------------
# A container that keeps crashing is often "running" at the moment of a check, so compare each
# container's restart count with the previous run. A redeploy recreates containers (count 0): no alert.
restarts_file="$STATE_DIR/restarts"
declare -A restarts_before=()
if [ -f "$restarts_file" ]; then
  while read -r rname rcount; do restarts_before["$rname"]="$rcount"; done < "$restarts_file"
fi
: > "$restarts_file.new"
for cid in $(docker compose ps -q 2>/dev/null); do
  read -r rname rcount < <(docker inspect --format '{{.Name}} {{.RestartCount}}' "$cid" 2>/dev/null) || continue
  rname="${rname#/}"
  echo "$rname $rcount" >> "$restarts_file.new"
  before="${restarts_before[$rname]:-}"
  if [ -n "$before" ] && [ "$rcount" -gt "$before" ]; then
    problem "Container $rname restarted $((rcount - before)) time(s) since the last check (total $rcount)"
  fi
done
mv "$restarts_file.new" "$restarts_file"

# CPU/memory per container: one sample.
while read -r name cpu mem; do
  [ -z "$name" ] && continue
  cpu="${cpu%\%}"; mem="${mem%\%}"
  awk -v v="$mem" -v t="$MEM_ALERT_PCT" 'BEGIN{exit !(v>=t)}' && problem "Container $name memory at ${mem}% of its limit"
  awk -v v="$cpu" -v t="$CPU_ALERT_PCT" 'BEGIN{exit !(v>=t)}' && note "Container $name CPU at ${cpu}% (sampled once)"
done < <(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemPerc}}' $(docker compose ps -q 2>/dev/null) 2>/dev/null)

# --- Host disk and database size ---------------------------------------------------------
docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
for path in / "$docker_root" "$(pwd)/db/backups"; do
  [ -e "$path" ] || continue
  used="$(df -P "$path" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')"
  [ -n "$used" ] && [ "$used" -ge "$DISK_ALERT_PCT" ] && problem "Disk ${used}% full at $path"
done
db_bytes="$(docker compose exec -T postgres psql -X -At --username "${POSTGRES_SUPERUSER:-postgres}" --dbname postgres \
  -c "SELECT pg_database_size('${DB_NAME:-esabong}')" 2>/dev/null | tr -dc '0-9')"
if [ -z "$db_bytes" ]; then
  problem "PostgreSQL did not answer a size query"
elif [ "$db_bytes" -ge $((DB_SIZE_ALERT_GB * 1024 * 1024 * 1024)) ]; then
  problem "Database is $((db_bytes / 1024 / 1024 / 1024)) GB (threshold ${DB_SIZE_ALERT_GB} GB)"
fi

# --- Error rates from logs ---------------------------------------------------------------
edge="$(docker compose logs --since "$WINDOW" --no-log-prefix caddy 2>/dev/null | grep '"status":' || true)"
total="$(printf '%s' "$edge" | grep -c '"status":' || true)"
errors="$(printf '%s' "$edge" | grep -cE '"status":5[0-9]{2}' || true)"
if [ "${total:-0}" -ge "$ERROR_MIN_REQUESTS" ] && [ $((errors * 100)) -gt $((total * ERROR_RATE_PCT)) ]; then
  problem "HTTP 5xx rate ${errors}/${total} requests in the last ${WINDOW}"
fi
ws_errors="$(docker compose logs --since "$WINDOW" --no-log-prefix api 2>/dev/null | grep -c 'ws_task_error' || true)"
[ "${ws_errors:-0}" -gt "$WS_ERRORS_MAX" ] && problem "${ws_errors} WebSocket task errors in the last ${WINDOW}"

# --- Backups -----------------------------------------------------------------------------
if [ -z "$(find db/backups -maxdepth 1 -name '*.dump' -mmin -$((BACKUP_MAX_AGE_HOURS * 60)) 2>/dev/null | head -n1)" ]; then
  problem "No database backup newer than ${BACKUP_MAX_AGE_HOURS} h in db/backups"
fi
if [ -f db/backups/.offsite-status ]; then
  read -r when result _ < db/backups/.offsite-status
  [ "$result" = "ok" ] || problem "Last off-site backup failed at $when: $(cut -d' ' -f3- db/backups/.offsite-status | cut -c1-160)"
  if [ -z "$(find db/backups -maxdepth 1 -name .offsite-status -mmin -$((BACKUP_MAX_AGE_HOURS * 60)) 2>/dev/null)" ]; then
    problem "Off-site backup has not run for over ${BACKUP_MAX_AGE_HOURS} h"
  fi
else
  note "Off-site backups not configured yet (no db/backups/.offsite-status)"
fi

# --- Report and alert --------------------------------------------------------------------
host="$(hostname)"
summary="$(printf '%s\n' "${problems[@]+"${problems[@]}"}" | sed '/^$/d')"
fingerprint="$(printf '%s' "$summary" | sed -E 's/[0-9]+/N/g' | (sha256sum 2>/dev/null || shasum -a 256) | cut -c1-16)"
state_file="$STATE_DIR/state"
prev_fp=""; prev_at=0
[ -f "$state_file" ] && read -r prev_fp prev_at < "$state_file"
now="$(date +%s)"

send() {  # send <text>
  local text="$1"
  if [ "${MONITOR_DRY_RUN:-0}" = "1" ]; then echo "[dry-run alert] $text"; return; fi
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    payload="$(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))' 2>/dev/null \
      || printf '{"text":"%s"}' "$(printf '%s' "$text" | sed 's/["\\]/ /g' | tr '\n' ' ')")"
    curl -s -o /dev/null --max-time 10 -H 'Content-Type: application/json' -d "$payload" "$ALERT_WEBHOOK_URL" || echo "webhook delivery failed" >&2
  fi
  if [ -n "${OCI_ONS_TOPIC_ID:-}" ] && command -v oci >/dev/null; then
    oci ons message publish --topic-id "$OCI_ONS_TOPIC_ID" --title "E-Sabong monitor: $host" --body "$text" >/dev/null 2>&1 \
      || echo "OCI Notifications delivery failed" >&2
  fi
  [ -z "${ALERT_WEBHOOK_URL:-}${OCI_ONS_TOPIC_ID:-}" ] && echo "No alert channel configured (ALERT_WEBHOOK_URL / OCI_ONS_TOPIC_ID)" >&2
}

for n in "${notes[@]+"${notes[@]}"}"; do echo "note: $n"; done
if [ -z "$summary" ]; then
  echo "OK: all checks passed on $host"
  [ -n "$prev_fp" ] && [ "$prev_fp" != "ok" ] && send "RESOLVED on $host: all checks pass again."
  echo "ok $now" > "$state_file"
  [ -n "${HEARTBEAT_URL:-}" ] && [ "${MONITOR_DRY_RUN:-0}" != "1" ] && curl -fsS -o /dev/null --max-time 10 "$HEARTBEAT_URL" || true
  exit 0
fi

echo "ALERT on $host:"; echo "$summary" | sed 's/^/  - /'
if [ "$fingerprint" != "$prev_fp" ] || [ $((now - prev_at)) -ge $((ALERT_REPEAT_MINUTES * 60)) ]; then
  send "ALERT on $host ($(date -u +%H:%MZ)):
$(echo "$summary" | sed 's/^/- /')"
  echo "$fingerprint $now" > "$state_file"
fi
exit 1
