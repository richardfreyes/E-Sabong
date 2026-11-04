# Monitoring, alerts and scheduled backups

No extra services or agents: one shell check every 5 minutes, two backup timers, container
healthchecks, JSON logs with rotation, and alert delivery through channels you already have.

## What is checked (`check.sh`)

| Signal | How | Alert when (default, override in `.monitor.env`) |
|---|---|---|
| API liveness | `GET https://$PLATFORM_HOST/api/health` | not 200 |
| PostgreSQL + Redis connectivity | `GET /api/ready` | either check not `ok` |
| Portals (Owner, Player, Admin) | `GET /`, tenant `/` and `/admin/` | not 200 (tenant checks need `MONITOR_TENANT_HOST`) |
| WebSocket path through the edge | HTTP/1.1 upgrade on `/api/ws` | not `101 Switching Protocols` |
| Containers incl. Celery worker | `docker compose ps` + healthchecks (`celery inspect ping`) | missing, not running, `unhealthy`; migrate exit ≠ 0 |
| Memory per container | `docker stats` vs limit | ≥ `MEM_ALERT_PCT` 90 % |
| CPU per container | `docker stats` (one sample) | ≥ `CPU_ALERT_PCT` 90 % → note only (single samples are noisy) |
| Disk | `df` on `/`, Docker root, `db/backups` | ≥ `DISK_ALERT_PCT` 85 % |
| Database storage | `pg_database_size` | ≥ `DB_SIZE_ALERT_GB` 40 |
| HTTP 5xx | Caddy JSON access log, last `MONITOR_WINDOW` 5m | > `ERROR_RATE_PCT` 2 % with ≥ `ERROR_MIN_REQUESTS` 20 requests |
| WebSocket failures | API log events `ws_task_error` (also logged: `ws_rejected`, `ws_connected`, `ws_closed`) | > `WS_ERRORS_MAX` 10 in the window |
| Backups | newest `db/backups/*.dump`; `db/backups/.offsite-status` | older than `BACKUP_MAX_AGE_HOURS` 26, or last off-site run failed |
| TLS certificates | `openssl s_client` per host | expires within `TLS_ALERT_DAYS` 14 |

An alert is sent when a problem first appears, repeated every `ALERT_REPEAT_MINUTES` (60)
while it lasts, and a `RESOLVED` message when everything passes again.

## How administrators receive alerts

Configure at least one channel in `/opt/esabong/.monitor.env` (`chmod 600`, git-ignored):

```env
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/…   # or Discord/Teams/Google Chat incoming webhook
OCI_ONS_TOPIC_ID=ocid1.onstopic.oc1…                   # OCI Notifications → email/SMS/PagerDuty subscribers
HEARTBEAT_URL=https://hc-ping.com/<uuid>               # dead-man's switch: alerts if the host/timer stops
MONITOR_TENANT_HOST=<one real tenant>.<base-domain>
```

- **OCI Notifications** needs the OCI CLI on the host with instance-principal auth
  (dynamic group + policy `allow dynamic-group esabong-hosts to use ons-topics in compartment …`).
- **The heartbeat is required for real coverage:** a host that is down cannot report itself.
  Any external uptime monitor pointed at `https://$PLATFORM_HOST/api/health` gives the same.
- **Host metrics in the OCI console (optional):** Compute instances report CPU, memory and
  disk metrics to OCI Monitoring. Add alarms there (e.g. `CpuUtilization > 85` for 15 min,
  `DiskBytesUsed`) with the same Notifications topic.

None of these credentials exist in this repository. Until one is set, `check.sh` prints
`No alert channel configured` and only the exit code and journal show problems.

## Install on the server (as root, checkout in `/opt/esabong`)

```bash
cp infrastructure/systemd/esabong-*.service infrastructure/systemd/esabong-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now esabong-monitor.timer esabong-backup.timer esabong-restore-drill.timer
systemctl list-timers 'esabong-*'
journalctl -u esabong-monitor -n 20          # last checks
MONITOR_DRY_RUN=1 infrastructure/monitoring/check.sh   # try it without sending alerts
```

Cron alternative: `infrastructure/systemd/crontab.example`.

## Logs

- **API, worker, beat:** structured JSON (structlog), one `request` line per HTTP request with
  `request_id`, `method`, `path` (never the query string), `status`, `duration_ms`, `tenant`.
  Passwords, tokens and cookies are not logged; WebSocket events log the tenant and close
  reason, never the token.
- **Caddy:** JSON access log on stdout with the `token` query parameter, `Authorization`,
  `Cookie` and `Set-Cookie` removed (verified on a live request).
- **Rotation:** Docker `json-file` driver, `LOG_MAX_SIZE` (20m) × `LOG_MAX_FILES` (5) per container.
- Read: `docker compose logs -f api`, `docker compose logs caddy | grep '"status":5'`.

## Off-site backups

`db/scripts/offsite-backup.sh` (nightly) and the weekly restore-drill timer. Settings go in
`/opt/esabong/.offsite.env` (`chmod 600`); the script header lists the Oracle Object Storage
values. Status for monitoring is written to `db/backups/.offsite-status`. Verified only
against a local-filesystem rclone remote so far — see `docs/production-readiness.md`.
