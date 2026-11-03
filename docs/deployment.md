# Deployment

> **Contabo VPS (UAT + production on one server):** follow
> [deployment-contabo.md](deployment-contabo.md). It uses two isolated Compose projects
> (`esabong-prod`, `esabong-uat`) behind one HTTPS gateway (`infrastructure/gateway/`). This
> page describes the general single-stack layout.

## Current release gate (2 November 2026)

Read the [current readiness report](readiness/2026-11-02/REPORT.md) before executing commands
below. **Technical staging: conditionally ready. Public launch: not ready.** No production
or candidate staging deployment was executed in this review. Existing development services
were preserved. Code/build/configuration checks are not a deployment authorization.

The source database was verified at **0012**. New forward migrations **0013 → 0014 → 0015**
are pending; 0012 was not rewritten. Rehearsals preserved the complete legacy points and
Arena Points wallet/ledger hashes, and a quiescent scratch backup restored exactly at 0015.
Before any data-bearing target migration, quiesce writes, back up/checksum, restore into a
fresh scratch database, verify hashes/constraints and rehearse supported rollback. The first
active-source comparison had usage-counter drift; it is not a successful quiescent drill.

The 0013 guard must remain installed during application rollback. Populated 0014/0015
simulation tables/receipts must never be dropped. Prefer a forward fix or compatible
application rollback; a whole-database restore requires separate approval and reconciliation
of all activity after the recovery point. Never run `down -v` as a release or rollback step.

### Prepared local staging target — approval required

Use a separate project, independent named volumes and private credentials. Start with
`.env.staging.example` outside Git; do not use the development `.env` or production secrets.
The prepared example is **production-mode application settings**, with intentional local
edge exceptions: loopback 8080/8443 and internal-CA TLS, not public certificates.

```bash
# Configuration check only; does not deploy or migrate.
docker compose -p esabong-staging --env-file /secure/staging.env \
  -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.staging.yml config --quiet

# Build review images without changing running services.
docker compose -p esabong-readiness build api caddy

# Check an already-built image with no network, volume mounts or Compose teardown.
python3 infrastructure/scripts/preflight.py --env-file /secure/staging.env \
  --compose-file docker-compose.yml --compose-file docker-compose.prod.yml \
  --skip-host --api-image esabong-readiness-api
```

Preflight no longer starts/builds a Compose stack or removes volumes. Without `--api-image`
it reports the image check as unexecuted. The strict production preflight deliberately
requires public 80/443 and ACME; validate the local staging exceptions separately using the
three-file merged configuration. Generated Compose JSON contains secrets; keep it private.

**Only after explicit staging deployment approval**, use the same reviewed configuration
and approved image artifacts for `up -d --wait`. A separate new project must not point at
existing external volumes, source databases, production Redis or customer media. Provision
staging-only accounts via approved setup; never put synthetic fixture data into production.

After deployment verify: migrate exited 0 at 0015, API/DB/Redis/Caddy/worker health, scheduler
execution, authentication/secure cookies, tenant hosts, admin/player routing, both socket
gateways, real Arena Points history/reconciliation, expected video error without credentials,
authorized playback with licensed credentials, and graceful API/worker restarts. Trust only
the staging CA for a local TLS drill; do not disable TLS verification globally. Remote
staging must use its own DNS and public certificate and repeat the host/firewall checks.

`REDIS_MAX_CONNECTIONS` (100), `REDIS_PUBSUB_MAX_CONNECTIONS` (1024) and
`REDIS_POOL_TIMEOUT_SECONDS` (5) are **per process**. Include both pools, API process count,
Celery clients and Redis memory/max-clients when sizing. These defaults are bounded capacity,
not a tested 1,000-client production SLA. The earlier peak benchmark failed and needs a
separate-machine staging rerun after the fix. Record/preserve the reviewed image IDs and
promote those identical artifacts rather than resolving fresh dependencies on production.

No production-host/DNS/ACME/SMTP/video/OCI/off-site or alert credentials were supplied for
this review. Obtain legal/privacy/video-rights and operational sign-offs before public use.
The deployment and rollback sections below are operator procedures, not already executed checks.

Everything runs from `docker-compose.yml`:

| Service | Image | Role |
|---|---|---|
| `postgres` | postgres:17-alpine | Database; roles created on first boot by `db/scripts/init-roles.sh` |
| `redis` | redis:7.4-alpine | Cache, pub/sub, Celery broker (password-protected, AOF on) |
| `migrate` | api image | One-shot `alembic upgrade head`; API starts only after it succeeds |
| `api` | api image (`api/Dockerfile`) | uvicorn, 4 workers, non-root, no published port |
| `worker`, `beat` | api image | Celery worker and scheduler |
| `caddy` | `infrastructure/docker/web.Dockerfile` | Builds the three portals; HTTPS, routing, security headers, static files |
| `mailpit` | profile `dev` only | Catches outgoing mail locally |

## 1. Server prerequisites

- Linux host with Docker Engine 24+ and the compose plugin. Start staging sizing at
  4 vCPU / 8 GiB RAM and measure: the current container memory caps total about 5.5 GiB
  before host overhead. A 2 vCPU / 4 GiB machine is a development profile requiring
  lower caps, not a verified peak-load production capacity.
- Ports 80 and 443 reachable from the internet (ACME HTTP/TLS challenges).
- DNS:
  - `A`/`AAAA` for `owner.<base>` → server
  - wildcard `A`/`AAAA` for `*.<base>` → server (tenant subdomains)
- An SMTP account for transactional mail.

## 2. Configure

```bash
git clone <repo> /opt/esabong && cd /opt/esabong
cp .env.example .env
```

Set at minimum:

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (enables startup checks: strong `JWT_SECRET`, tenant header disabled) |
| `BASE_DOMAIN`, `PLATFORM_HOST` | e.g. `platform.example.com`, `owner.platform.example.com` |
| `POSTGRES_SUPERUSER_PASSWORD`, `DB_OWNER_PASSWORD`, `DB_APP_PASSWORD`, `DB_PLATFORM_PASSWORD`, `REDIS_PASSWORD` | distinct random values (`openssl rand -base64 32`) |
| `JWT_SECRET` | ≥ 32 random bytes |
| `ACME_EMAIL`, `CADDY_TLS_MODE=acme` | certificate contact; public certificates |
| `SMTP_*`, `MAIL_FROM` | mail delivery |
| `LICENSE_PRIVATE_KEY_PEM`, `LICENSE_PUBLIC_KEY_PEM` | only if you sell dedicated licenses (`make license-keys`) |

`COOKIE_SECURE` must stay `true` in production. `ALLOW_TENANT_HEADER` must be `false`
(the API refuses to start otherwise).

On a production host also set `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` in
`.env`, so every `docker compose` command (and `db/scripts/*.sh`) uses the production overlay:

| Overlay effect | Why |
|---|---|
| `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `PUBLIC_SCHEME=https`, `ALLOW_TENANT_HEADER=false` on every API process | startup refuses unsafe settings (below) |
| Postgres and Redis publish no host port; the `backend` network is `internal` | only `api`, `worker`, `beat`, `migrate` reach them; Caddy sits on `edge` only |
| `CADDY_TLS_MODE=acme`, `ACME_EMAIL` and `SMTP_HOST` required | public certificates and a real contact |
| CPU/memory limits per service (`API_MEMORY`, `PG_MEMORY`, … to tune) | a runaway container cannot starve the host |

Both files get container log rotation (`LOG_MAX_SIZE`, default 20 MB × `LOG_MAX_FILES` 5) and
healthchecks for `postgres`, `redis`, `api`, `worker` (Celery ping) and `caddy`.

**Startup checks.** With `ENVIRONMENT=production` the API, worker and migrations exit at
startup, listing every problem at once, if any of these holds: `JWT_SECRET` shorter than 32
bytes or a placeholder; `ALLOW_TENANT_HEADER=true`; `COOKIE_SECURE=false`; `PUBLIC_SCHEME`
not `https`; `BASE_DOMAIN`/`PLATFORM_HOST` on `localhost` or `example.com`; a database URL
with a default (`app`, `platform`, `owner`) or `CHANGE_ME` password; a Redis/Celery URL
without a password; `CORS_ORIGINS` containing `*`; a malformed `VIDEO_CREDENTIALS_KEY`.
Interactive API docs (`/api/docs`, `/api/openapi.json`) are off in production unless
`API_DOCS_ENABLED=true`; the contract is always in `docs/openapi.json`.

**Edge rules.** Caddy answers `404` for `/api/v1/internal/*` (the on-demand TLS `ask`
endpoint is called by Caddy directly on `api:8000`, never through the public edge).

## 3. First start

```bash
docker compose up -d --build
docker compose ps                                     # api healthy, migrate exited 0
docker compose run --rm api python -m app.cli seed    # plan catalogue (inactive, unpriced) + feature flags
docker compose run --rm api python -m app.cli create-platform-admin --email ops@example.com --name "Ops"
```

Sign in at `https://owner.<base>`, open **Plans**, enter each plan's PHP prices, tick
**These PHP prices are approved for sale** and mark it available: unapproved, inactive or
legacy-currency (e.g. old USD) plans cannot be used for new subscriptions, plan changes or
signups, and changing a price withdraws approval until it is approved again. Then either provision a tenant directly
(**Tenants → New tenant**) or approve requests submitted to
`POST /api/v1/public/signup-requests`. The tenant admin receives a temporary
password by email and must change it at first sign-in.

## 4. HTTPS and custom domains

Caddy issues certificates **on demand** at the first TLS handshake for a host,
but only after asking `GET /api/v1/internal/tls-ask?domain=…`, which approves:

- the platform host,
- `<slug>.<base>` for existing tenants,
- custom domains whose DNS ownership has been verified.

Unknown hosts are refused, so nobody can make the server request certificates
for arbitrary names. To add a custom domain, a tenant admin (or platform staff)
adds it under **Settings → Domains**, then publishes:

```
_esabong-verify.play.customer.com  TXT    "<token shown in the UI>"
play.customer.com                  CNAME  <base-domain>
```

Verification runs on demand (**Check DNS**) and hourly in the worker. Once
verified, the domain serves the tenant's player portal at `/` and admin at `/admin/`.

`CADDY_TLS_MODE=internal` uses Caddy's local CA (development).

## 5. Operations

Monitoring and alerting (health, portals, WebSocket, containers, CPU/memory, disk, database
size, 5xx rate, backups, certificate expiry; webhook / OCI Notifications / heartbeat):
`infrastructure/monitoring/README.md`.

| Task | Command |
|---|---|
| Logs (JSON, one line per request with `request_id` and `tenant`) | `docker compose logs -f api` |
| Readiness (DB + Redis) | `curl https://owner.<base>/api/ready` |
| Run billing now | owner console API `POST /api/v1/platform/billing/run` (super admin) or wait for the hourly task |
| Scale API | raise uvicorn `--workers` or run more `api` replicas behind Caddy; realtime fan-out works across replicas via Redis |

**Monitoring suggestions:** alert on `/api/ready` failures, 5xx rate from Caddy
access logs, Celery worker liveness, Postgres disk usage, and certificate errors
in Caddy logs.

## 6. Backups and restore

Automated: `infrastructure/systemd/esabong-backup.timer` (nightly `db/scripts/offsite-backup.sh`:
backup → restore drill → upload → download-back checksum → remote retention) and
`esabong-restore-drill.timer` (weekly, also restores the downloaded off-site copy into a scratch
database). Setup and the Oracle Object Storage settings: `infrastructure/monitoring/README.md`
and the header of `offsite-backup.sh`. Backup files are created mode 600 in a mode-700 directory.

All ops scripts read `.env` with `db/scripts/lib-env.sh` (parsed like compose; never executed).

```bash
db/scripts/backup.sh                         # pg_dump (custom format, verified) + media archive → db/backups/
RETENTION_DAYS=30 db/scripts/backup.sh
```

Schedule it (cron/systemd timer) and copy `db/backups/` off the server. The dump
is taken as the superuser so every tenant's rows are included; grants are kept.

```bash
db/scripts/restore.sh db/backups/esabong-<stamp>.dump [db/backups/media-<stamp>.tar.gz]
```

**Verify every backup without touching the live database:**

```bash
db/scripts/verify-backup.sh db/backups/esabong-<stamp>.dump
```

It checks the `.sha256` written by `backup.sh`, restores into a scratch database
(`<DB_NAME>_restore_check`), compares every table's row count, RLS policies, FORCE RLS
flags, application-role grants, triggers and the Alembic revision with the live database,
prints `RESTORE VERIFIED` (exit 0) or the differences (exit 1), and drops the scratch
database. Run it right after the backup (row counts are compared exactly). Last local
run (2026-11-01): 41 tables, 8,970 rows, 33 policies, 8 triggers, revision 0011 — verified.

Restore stops the app services, recreates the database, loads the dump as the
superuser (forced RLS would reject the load as the owner role), returns
ownership to `esabong_owner`, applies newer migrations and restarts. Test a
restore on a staging host regularly; the procedure was verified locally with a
round trip that preserved row counts, RLS policies and grants.

## 7. Upgrades

```bash
git fetch --tags && git checkout <release-tag>
db/scripts/backup.sh && db/scripts/verify-backup.sh db/backups/<new dump>
docker compose build
docker compose up -d        # migrate runs before the new API starts
```

Migrations are forward-only in production; `alembic downgrade` exists for
development (and is exercised by every backend test run: `downgrade base` → `upgrade head`).

### Pre-deploy preflight

Read-only; run on the server before the first start and after any configuration change:

```bash
python3 infrastructure/scripts/preflight.py      # exit 1 on any FAIL
```

It checks the merged compose config (only Caddy publishes 80/443; Postgres/Redis unpublished on
an internal network; restart policies, memory limits, healthchecks, named volumes, log
rotation, `ENVIRONMENT=production`, `CADDY_TLS_MODE=acme`), secret strength and distinctness
(values never printed) plus the API's own `production_problems()`, file modes of `.env`,
`.offsite.env`, `.monitor.env` and `db/backups`, listening sockets on the host, and — with the
OCI CLI and `OCI_SECURITY_LIST_IDS` / `OCI_NSG_IDS` set — that internet ingress allows only
TCP 80/443 (SSH is reported as a warning). It never changes firewall rules.

### Post-deploy smoke test

Run after every deploy and every restore; roll back on any failure:

```bash
cd api && SMOKE_OWNER_PASSWORD='…' SMOKE_TENANT_PASSWORD='…' .venv/bin/python scripts/smoke.py \
  --platform https://owner.<base> --tenant https://<tenant>.<base> \
  --owner-email <owner email> --tenant-user <a dedicated smoke-test player>
```

24 checks: liveness and readiness (PostgreSQL + Redis), internal endpoints blocked at the edge,
API docs off, HSTS/CSP, HTTP→HTTPS redirect, all three portals, owner and tenant sign-in,
plan review state, a WebSocket that connects and answers a ping, and refusal of an invalid
WebSocket token. Then `docker compose ps` (all healthy, migrate exited 0) and
`infrastructure/monitoring/check.sh` (exit 0).

### Rollback

1. **Application only (no new migration in the release):** check out the previous tag,
   `docker compose build && docker compose up -d`. Images are rebuilt from source, so tag
   every release (`git tag release-YYYYMMDD`) and keep the previous tag on the host.
2. **Release included a migration:** migrations are not downgraded in production. Either
   ship a forward fix, or restore the backup taken immediately before the upgrade
   (step 2 of the upgrade above) with `db/scripts/restore.sh`, then deploy the previous
   tag. Data written between the backup and the restore is lost — announce a maintenance
   window and stop traffic first if that matters.
3. After either path, run the smoke test above. Write schema changes as expand → migrate → contract when a change
must stay compatible with the previous release during rollout.

## 8. Dedicated (licensed) installations

Runbook for a customer who buys a license:

1. **Control plane:** provision the tenant with deployment mode *Dedicated* and no
   plan; issue a license under **Licenses → Issue license** (copy the key — only a
   hash is stored); register the host under **Deployments**. Optionally start a
   maintenance plan subscription.
2. **Customer server:** install as in sections 1–3, with:

   ```env
   DEPLOYMENT_MODE=dedicated
   DEDICATED_TENANT_SLUG=<same slug as on the control plane>
   LICENSE_KEY=ESB-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX
   LICENSE_PUBLIC_KEY_PEM=<control plane public key>
   LICENSE_HEARTBEAT_URL=https://owner.<base>/api/v1/licensing/heartbeat
   BASE_DOMAIN=<customer domain>   PLATFORM_HOST=<an internal-only host>
   ```

3. Activate first — until then the install answers API requests with
   `403 license_invalid`:

   ```bash
   docker compose run --rm api python -m app.cli activate-license \
     --url https://owner.<base>/api/v1/licensing/activate
   ```

   Put the printed `LICENSE_TOKEN=` line into `.env` and `docker compose up -d`.
   Then create the tenant with the licensed slug (no plan):

   ```bash
   docker compose run --rm api python -m app.cli create-tenant \
     --slug <slug> --name "<Customer name>" --admin-email it@customer.com
   ```

   The command prints the admin sign-in URL and a temporary password.
4. The install verifies the signed token offline. Every 6 hours `beat` sends a
   heartbeat that refreshes the token and reports version; the deployment shows
   as *active* with its last heartbeat in the owner console. Revoking the license
   stops API traffic at the next heartbeat (`403 license_invalid`).

Provisioning the customer's server itself (VM, DNS, firewall) is manual — this
runbook is the procedure; there is no infrastructure automation in this repository.

## 9. Security checklist

- [ ] Unique secrets in `.env`; file readable only by the deploy user.
- [ ] `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `ALLOW_TENANT_HEADER=false`.
- [ ] Postgres and Redis ports not exposed publicly (compose binds them to 127.0.0.1; remove the `ports:` entries if unused).
- [ ] Firewall: only 22 (restricted), 80, 443 inbound.
- [ ] Off-host, encrypted backups; restore tested.
- [ ] License private key only on the control plane.
- [ ] OS and image updates applied regularly (`docker compose pull && docker compose up -d --build`).
