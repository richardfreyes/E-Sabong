# Deployment

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

- Linux host with Docker Engine 24+ and the compose plugin; 2 vCPU / 4 GB RAM minimum.
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

## 3. First start

```bash
docker compose up -d --build
docker compose ps                                     # api healthy, migrate exited 0
docker compose run --rm api python -m app.cli seed    # default plans + feature flags
docker compose run --rm api python -m app.cli create-platform-admin --email ops@example.com --name "Ops"
```

Sign in at `https://owner.<base>` and either provision a tenant directly
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

```bash
db/scripts/backup.sh                         # pg_dump (custom format, verified) + media archive → db/backups/
RETENTION_DAYS=30 db/scripts/backup.sh
```

Schedule it (cron/systemd timer) and copy `db/backups/` off the server. The dump
is taken as the superuser so every tenant's rows are included; grants are kept.

```bash
db/scripts/restore.sh db/backups/esabong-<stamp>.dump [db/backups/media-<stamp>.tar.gz]
```

Restore stops the app services, recreates the database, loads the dump as the
superuser (forced RLS would reject the load as the owner role), returns
ownership to `esabong_owner`, applies newer migrations and restarts. Test a
restore on a staging host regularly; the procedure was verified locally with a
round trip that preserved row counts, RLS policies and grants.

## 7. Upgrades

```bash
git pull
db/scripts/backup.sh
docker compose build
docker compose up -d        # migrate runs before the new API starts
```

Migrations are forward-only in production; `alembic downgrade` exists for
development. Write schema changes as expand → migrate → contract when a change
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
