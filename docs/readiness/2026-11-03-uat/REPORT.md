# Final UAT validation and deployment preparation — 3 November 2026

Validated base: `b65e519889d318da165487dd6d576455987d14fd` on `production-readiness`.
No push, tag, VPS writes, deployment, UAT migration or production migration was performed.
Only the integration suite's isolated `esabong_test` fixture rebuilt its test schema.

## Local results

| Check | Result |
|---|---|
| Backend integration, real isolated PostgreSQL/Redis | 228 passed, 100.08 s |
| Backend unit, separate process | 141 passed, 1.57 s |
| Ruff check / format check | clean, 170 files |
| Frontend Owner / Admin / Player unit | 60 / 58 / 83 passed |
| Angular production builds | all passed; Owner built with `/owner/` |
| General Playwright, including real local MediaMTX | 34 passed, 7 skipped, 2.7 min |
| Separate Contabo gateway browser suite | 6 passed, 17.6 s |
| Repository smoke through local UAT-only HTTPS gateway | 25 passed, 0 failed |
| UAT-only TLS | production SNI refused |
| Gate credential in gateway logs | absent |
| Both Compose configurations | ownership, mounts, networks, volumes and service URLs passed |
| Caddy | full gateway, UAT-only gateway, origin and single-host configs valid |
| Gateway script modes | UAT-only, dual and invalid-mode rejection passed |
| ShellCheck | gateway, entrypoint, deployment, network probe, RTMP and backup scripts clean |
| Local synthetic UAT backup/restore | verified checksum, content fingerprints, schema safeguards and revision |

The seven general-suite skips are the six gateway tests (run separately above) and the
opt-in visual inventory. Streaming was enabled and passed. The first run had 32 passes,
eight skips and one failure: the separate `demo` tenant fixture did not exist, so a test
expecting cross-tenant 401 received tenant-not-found 404. Creating that synthetic fixture
in the isolated E2E database resolved the failure; no application code was changed for it.
The first sandboxed Angular build aborted without a diagnostic; the unsandboxed retry
built all three portals. Player unit tests emit jsdom media-method warnings but pass.

Authentication coverage includes Owner, Admin, Player and Declarator; assigned-event access;
reload/concurrent-reload persistence; independent portal sign-out; player denial at the admin
portal; platform/tenant token rejection; root-domain routing; and live WebSocket updates.
Streaming coverage includes admin preview, player WebRTC frames, Declarator monitoring,
LL-HLS fallback, source restart recovery and hiding a stream. Real OBS/WHIP publishing over
the public VPS uplink has not been tested. Declarators monitor assigned events; this release
does not add a Declarator publishing interface or permission.

Local targets: `esabong_test` + Redis 14/15; existing synthetic E2E DB
`esabong_e2e_20261103` + Redis 12/13; APIs 8100/8110/8111; Angular ports 4300–4302;
new validation gateway 18081/18444 and MediaMTX 18888/18889/19997/28189.
The gateway fixture used production Angular bundles, local Caddy certificates and a
development-mode isolated API. Production startup safety was checked by configuration
tests and Compose validation; this is not evidence of public ACME issuance.

Backup drill used the existing local `esabong-uat` rehearsal only: 48 tables, 421 rows,
40 RLS policies, 27 triggers, revision 0016 matched after restore. Retained dump:
`/private/tmp/esabong-final-uat-validation/backup-drill/uat/db/backups/uat-esabong-20261103T095612Z.dump`.
Retained scratch DB: `esabong_restore_final_uat_20261103` in that rehearsal's PostgreSQL.
No migration command ran against it. Live-stream telemetry exclusions follow the existing
backup verifier; stream configuration and all other content are compared.

The three new validation containers and three API processes were stopped after checks;
they, their environments and the original rehearsal data were retained. Raw local logs are under `/private/tmp/esabong-final-uat-validation/` and
`/tmp/esabong-final-uat-*.log`. Treat temporary environments and logs as private, do not
commit them, and retain databases/backups until cleanup is approved.

## Release and security review

Live GitHub inspection confirmed `production-readiness` remains at `cccee4d`; the proposed
`uat-20261103-rc.1` tag does not exist. Six commits are present locally in the approved order;
no history was rewritten:

1. `3af324c` — Compose/environment isolation.
2. `1ce837d` — shared HTTPS gateway and portal routing.
3. `d75a5d6` — root tenant and owner portal.
4. `97cf204` — UAT access gate and synthetic-data controls.
5. `0cf5367` — separate streaming ports and RTMP allowlists.
6. `b65e519` — operation guards, documentation and tests.

The tree was clean at validation start. The seven follow-up entries were reviewed and approved
for a local commit containing only the tested gateway correction and documentation/handoff.
All 1,663 base tracked paths and the six original commit patches
were scanned for known private-key, cloud-key, GitHub-token and JWT patterns. No matching
credentials were found. No real `.env`, private-key/backup files, `dist`, `node_modules` or
`.venv` paths are tracked. Added release files contain no binary changes. The largest existing
tracked asset is about 0.98 MB; existing images, demo video and OpenAPI are intentional.
Templates use placeholders; development fixture passwords remain public test defaults and
are refused by production-mode `demo-tenant`. Migration 0016 is tracked in
`db/migrations/versions/20261103_0016_video_streams.py`.

This was a pattern-based credential scan and manual template review, not a dependency
vulnerability audit or proof that every possible secret format is absent.

Approved local follow-up correction: UAT-only gateway mode. The previous full gateway
enabled production host routing and production on-demand TLS as soon as it started. New
`Caddyfile.uat-only` and `docker-compose.uat-only.yml`, selected by `GATEWAY_MODE=uat-only`,
expose only UAT and remove the production network. The template selects this mode; existing
env files without the setting retain dual behavior. Set the mode explicitly. The UAT gate
matches the full gateway's gate. The follow-up commit includes these files, the gateway
script and documentation; use its full SHA, reported by `git rev-parse HEAD`, for the next
push/tag approval. No tag, push or deployment is authorized by the local commit approval.

Isolation checks establish distinct Compose project/volume/network ownership, internal
PostgreSQL/Redis with no host ports, own service URLs and fresh distinct secret values.
Authentication tests verify tenant/platform boundaries. Host-only cookies and distinct
JWT secrets provide the planned cross-environment session boundary. Actual VPS environment
secrets do not exist yet and could not be compared. The Linux cross-environment network
probe remains an acceptance check after both environments are separately approved and
running; do not start production just to run that probe. OrbStack is not evidence of Linux
bridge isolation (see main runbook §14).

## VPS and domain inventory — read only

SSH succeeds using the existing workstation key `~/.ssh/contabo_esabong` with
`IdentitiesOnly=yes`. Private-key contents were not read or displayed.

- `/opt/esabong` is a clean checkout at `cccee4d`, branch `production-readiness`.
- Remote is `git@github-esabong:richardfreyes/E-Sabong.git`.
- No child `uat` checkout exists; preserve the existing checkout and its files.
- Ubuntu target is x86_64, six CPUs, `MemTotal=12247540 kB`, approximately 94 GB disk available.
- Docker Compose reports v5.7.0. Docker socket is `660 root:docker`.
- `deploy` is not in the Docker group. `sudo -n` requires a password. Container inventory
  and firewall state could not be read; listener inventory showed SSH only.
- UAT A record is `46.250.249.61`; AAAA and CAA queries returned no records.
- Production DNS and services were not changed. Public ACME issuance is still unverified.

The dual-stack example caps total 10.25 GiB, leaving about 1.43 GiB of actual host RAM.
The checker defaults to a nominal 12 GiB and reserves 1.5 GiB. Before a later production
deployment, pass the actual memory in bytes (`12541480960`) and review that small reserve
shortfall. UAT plus gateway alone is about 3 GiB; no production resource settings were changed.

## Approval sequence and release commands

Proposed tag: **`uat-20261103-rc.1`** (not created).
Original base SHA: **`b65e519889d318da165487dd6d576455987d14fd`**.
The corrected release is the approved follow-up commit directly after this base. Obtain
its full SHA with `git rev-parse HEAD` and compare it with the reported approved commit.

1. Follow-up local commit **approved**. Subject:
   `[ENG-XXXX] #fix - Keep production unexposed during first UAT deployment`.
2. Verify a clean tree and record its full SHA as `RELEASE_SHA`.
3. Approve the exact SHA and tag for GitHub push. Only then run:

```bash
git switch production-readiness
git status --short                        # must be empty
git rev-parse HEAD                        # must equal the approved RELEASE_SHA
git log --oneline -7
git push origin production-readiness
git tag -a uat-20261103-rc.1 "$RELEASE_SHA" -m "Validated Contabo UAT release candidate 1"
git push origin refs/tags/uat-20261103-rc.1
```

Set `RELEASE_SHA` to the reviewed full hash, not an inferred or moving branch name. No force
push, existing tag replacement, or unrelated-history changes are needed.

4. Separately approve VPS UAT preparation/deployment and deliberately choose Docker access
   using the security guidance below. No group or socket permission changes are authorized.
5. Review the actual UAT database revision and exact pending revisions after the guarded
   deployment stops. Separately approve **only** `uat:0016` before the migration command.
6. Run UAT acceptance checks. Production remains disabled; any production release requires
   a later, separate decision and deployment/migration approval.

### Docker access decision — recommendation only; no VPS changes made

For this existing rootful Docker installation, the conservative immediate choice is an
attended deployment by a trusted administrator using password-authenticated sudo for Docker
operations. Keep `deploy` out of the `docker` group and retain the root-owned socket at
`660 root:docker`. This avoids giving every ordinary `deploy` process permanent Docker
access; an authorized unrestricted Docker command still has root-equivalent power.
Docker documents that group membership grants root-level privileges and describes sudo
access to the root-owned socket in its [Linux post-installation guidance](https://docs.docker.com/engine/install/linux-postinstall/).

The repository scripts invoke `docker` directly, including from child Python processes.
They currently require a reviewed way to obtain that attended access. Do not assume a
shell alias or a previous `sudo -v` makes those calls privileged. Do not run user-writable
checkout scripts as root, enable broad passwordless `sudo docker`, or treat a wrapper around
arbitrary Docker arguments as least privilege. An automated privileged deployment service
would require root-owned code and configuration, pinned reviewed releases, strict input
validation and control over Compose files, host mounts, environment files and all other
privileged inputs. That design is a separate approval and implementation step.

Never solve this with world-writable socket permissions, broad socket ACLs, or an
unauthenticated Docker TCP endpoint. Permanent Docker-group membership may be chosen only
with explicit acceptance of its root-equivalent trust boundary; it is not this report's
default recommendation. No account, sudoers, daemon, socket or firewall changes were made.

For stronger isolation, evaluate [Docker rootless mode](https://docs.docker.com/engine/security/rootless/),
which runs both daemon and containers without host root. It is not a drop-in change for this
validated deployment: low ports, gateway networks, media networking, iptables rules, cgroups,
systemd and existing volumes need a separate compatibility review. Do not switch the VPS
daemon or migrate its volumes as part of this local commit.

## Exact UAT deployment procedure — future approved actions only

The main [Contabo runbook](../../deployment-contabo.md) remains the implementation reference.
Use this first-deployment sequence to preserve the existing checkout and avoid creating or
starting production. Do not rerun OS installation or modify production DNS.

After the release is on GitHub, Docker access works, and VPS preparation is approved:

```bash
ssh -i ~/.ssh/contabo_esabong -o IdentitiesOnly=yes deploy@46.250.249.61
git -C /opt/esabong status --short          # review; never reset this checkout
git -C /opt/esabong log -1 --oneline
docker ps --format '{{.Names}} {{.Ports}}' # inspect actual containers before proceeding
ss -lntup                                # verify 80/443 and UAT media ports are available

test ! -e /opt/esabong/uat
git clone --branch production-readiness git@github-esabong:richardfreyes/E-Sabong.git /opt/esabong/uat
test ! -e /opt/esabong/gateway
git clone --branch production-readiness git@github-esabong:richardfreyes/E-Sabong.git /opt/esabong/gateway
git -C /opt/esabong/uat checkout --detach uat-20261103-rc.1
git -C /opt/esabong/gateway checkout --detach uat-20261103-rc.1
git -C /opt/esabong/uat rev-parse HEAD       # compare with approved SHA
git -C /opt/esabong/gateway rev-parse HEAD
install -d -m 700 /opt/esabong/uat/db/backups
```

Run each existence check as a stopping condition; if a target exists, inspect it instead of
continuing to clone. Never move the existing root checkout or copy its `.env`/database into
UAT. The gateway gets its own checkout, with no production application checkout needed yet.
Never run `git clean` from the existing parent checkout: these nested checkouts are real work.

Prepare `/opt/esabong/uat/.env` from `.env.contabo.uat.example`, and
`/opt/esabong/gateway/infrastructure/gateway/.env` from that directory's `.env.example`.
Do not overwrite an existing file. Use `umask 077` and mode 600. Generate fresh secrets
using §4's `fill_secrets` function. Generate the gate token with `openssl rand -hex 24`
into its `.env`, without printing it. Fill mail fields using a UAT test mailbox. Store access
credentials in the team's password manager. No production `.env` is required for UAT-only
deployment. The private env files generated locally during validation are throwaway fixtures,
not configured server mail credentials and not deployment inputs.

Required values (secret values intentionally omitted):

| Category | Variables / UAT values |
|---|---|
| Identity | `ESB_STACK=uat`; `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml:docker-compose.contabo.yml`; `COMPOSE_PROFILES=streaming` |
| Runtime | `ENVIRONMENT=production`; `DEPLOYMENT_MODE=shared`; `BASE_DOMAIN=uat.swcmain183.live`; `PLATFORM_HOST=uat.swcmain183.live`; `ROOT_TENANT_SLUG=uat-demo`; `PUBLIC_SCHEME=https`; `COOKIE_SECURE=true`; `ALLOW_TENANT_HEADER=false`; `API_DOCS_ENABLED=` (disabled by production default) |
| Synthetic | `SYNTHETIC_DATA_ENABLED=true`; `STREAM_TEST_SOURCE=true` |
| Database | `DB_NAME=esabong` in UAT's own PostgreSQL; fresh `POSTGRES_SUPERUSER_PASSWORD`, `DB_OWNER_PASSWORD`, `DB_APP_PASSWORD`, `DB_PLATFORM_PASSWORD` |
| Auth/cache | fresh `JWT_SECRET` (64+ chars), `REDIS_PASSWORD`; own Redis service and volume |
| Media | fresh `VIDEO_CREDENTIALS_KEY` (Fernet), `MEDIAMTX_API_PASSWORD` (24+ chars); `STREAM_PUBLIC_HOSTS=46.250.249.61`; RTMP 1945, RTMPS 1946 loopback, WebRTC TCP/UDP 8190 |
| Mail | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `MAIL_FROM` (UAT provider/mailbox) |
| Gateway | `GATEWAY_MODE=uat-only`; `UAT_HOST=uat.swcmain183.live`; `ACME_EMAIL`; fresh `UAT_ACCESS_TOKEN` (32+ alphanumeric); specific tester/VPS public IPs in `UAT_ALLOWED_IPS`; 80/443 |
| Limits | retain UAT example CPU/memory/pids/worker values; no `COMPOSE_PROJECT_NAME` or exported overrides |

The same `DB_NAME` inside two separate PostgreSQL containers does not share a database.
Never point database/Redis URLs at the existing root stack or production, and never reuse
their secrets. If a real production environment is later configured, run the two-env secret
and ownership checker against the actual files before enabling it; do not create production
secrets merely to make a UAT-only check pass.

Next, after approved configuration and firewall preparation (only UAT ports 80/443 and
8190 TCP/UDP; RTMP 1945 only for allowlisted broadcasters):

```bash
cd /opt/esabong/gateway
infrastructure/scripts/gateway.sh networks  # UAT-only mode creates only UAT gateway network
infrastructure/scripts/gateway.sh validate

cd /opt/esabong/uat
infrastructure/scripts/deploy.sh uat-20261103-rc.1
```

This is a deployment-preparation action: it builds images and starts UAT PostgreSQL/Redis,
then stops if migrations are pending. It is intentionally **not run during this preparation**.
Do not bypass this guard with `docker compose up` or start `migrate` manually.

### Migration plan and separate approval

The local chain is linear: base → 0001 → … → 0015 → **0016 (head)**. A genuinely empty new
UAT database needs the full chain, including business-schema and historical guard revisions;
it is not an `0016`-only operation. If the database already exists, use its actual current
revision and review the exact range printed by `deploy.sh`. Production DBs must never be
queried to determine UAT's state. Revision 0016 creates `video_streams`, its indexes,
constraints, forced tenant RLS and role grants; it does not alter existing application rows.

After reviewing the actual printed plan, obtain explicit UAT migration approval, then:

```bash
cd /opt/esabong/uat
APPROVED_MIGRATIONS=uat:0016 infrastructure/scripts/deploy.sh uat-20261103-rc.1
docker compose run --rm api python -m app.cli seed
docker compose run --rm api python -m app.cli create-platform-admin --email <UAT_OWNER_EMAIL> --name 'UAT Owner'
```

The owner command prompts for its password. For `demo-tenant`, avoid the existing CLI's
password-printing summary and command-line password argument: call it with a hidden prompt
and suppress its stdout in memory (the function is unchanged):

```bash
docker compose run --rm api python -c 'import asyncio,contextlib,getpass,io; from app.cli import demo_tenant; password=getpass.getpass("UAT synthetic account password: "); output=io.StringIO();
with contextlib.redirect_stdout(output): asyncio.run(demo_tenant("uat-demo", password))
print("UAT synthetic accounts prepared; password output suppressed")'
docker compose run --rm api python -m app.cli demo-derby --slug uat-demo --fights 40

cd /opt/esabong/gateway
infrastructure/scripts/gateway.sh up
```

Use strong private passwords rather than the development default. Do not capture the password
prompt in session recordings. Synthetic account creation is an approved deployment step,
not part of the migration plan. Do not load production dumps. Gateway `up` in UAT-only mode
requests a certificate for UAT only; it does not configure the production root or catch-all.
Ports 80/443 must be externally reachable for ACME. Never use `--insecure` for public acceptance.

## Post-deployment acceptance — not executed on the VPS

Use main runbook §10 smoke tests with `--owner-portal-path /owner/`, UAT host, dedicated
synthetic credentials and an allowlisted tester address. Supply smoke passwords through
`SMOKE_OWNER_PASSWORD` / `SMOKE_TENANT_PASSWORD`; keep any gate cookie out of shell history,
process arguments and logs. Run all six Contabo browser tests with credentials in the
process environment, not literal command-line assignments stored in history.

- Check `/`, `/admin/`, `/owner/`, `/admin/declarator`, public tenant config, readiness,
  APIs and WebSocket frames over valid HTTPS; verify assigned Declarator access.
- Confirm no access without the gate, wrong credentials are denied, sign-in sets the Secure
  HttpOnly cookie, and no gate credential appears in URLs, Referer or logs.
- Check independent Owner/Admin/Player sessions after refresh and sign-out.
- Check UAT PostgreSQL/Redis/worker/beat health. During an approved UAT restart, verify data
  persistence. Preserve volumes; never use `down -v` or `volume prune`.
- Run `PRUNE_BACKUPS=0 db/scripts/backup.sh`; then `KEEP=1` and a fresh
  `SCRATCH_DB_NAME=esabong_restore_<unique_stamp>` with `verify-backup.sh`. Keep the dump
  and restored DB until deletion is approved. Do not use `restore.sh` for a drill: it replaces
  the live DB and can run migrations.
- Verify synthetic WebRTC and HLS playback, source restart recovery and token denial. Test
  OBS WHIP from an allowlisted address with an authorized synthetic source; verify ingest
  key handling. If RTMP is needed, install only the UAT 1945 allowlist using §9, never the
  combined production/UAT commands. Do not run viewer load tests on the VPS.
- Run `probe-isolation.sh` only when both real environments have separately been approved
  and are running. It correctly exits 2 when production is absent; record this as pending,
  not a pass. While UAT-only, inspect networks/volumes and confirm there is no production
  gateway network attachment, production TLS site, or production upstream.
- Recheck the original checkout, existing container IDs and production DNS against the
  pre-deployment inventory. Preserve all original files, data and backups.

Remaining external blockers: approved Docker access method; actual UAT mailbox/ACME email and tester IPs;
approved push/tag; explicit VPS deployment and exact UAT migration
approvals; public TLS/firewall/OBS/systemd checks; and eventual Linux cross-environment probe.
No production deployment is authorized by this report.
