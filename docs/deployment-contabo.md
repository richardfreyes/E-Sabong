# Deploying E-Sabong on one Contabo Cloud VPS 6 — isolated UAT and Production

Runbook for **two fully separate environments** of the whole platform (PostgreSQL, Redis,
FastAPI, Celery worker + beat, the three Angular portals, MediaMTX live streaming) on **one**
Contabo Cloud VPS 6 (`46.250.249.61`, Ubuntu 24.04 LTS, 6 vCPU, 12 GB RAM, 100 GB NVMe,
300 Mbit/s port), behind one shared HTTPS gateway.

| Environment | Player | Admin (+ Declarator) | Owner | API |
|---|---|---|---|---|
| **Production** | `https://swcmain183.live/` | `https://swcmain183.live/admin/` | `https://swcmain183.live/owner/` | `https://swcmain183.live/api/v1/` |
| **UAT** (restricted) | `https://uat.swcmain183.live/` | `https://uat.swcmain183.live/admin/` | `https://uat.swcmain183.live/owner/` | `https://uat.swcmain183.live/api/v1/` |

It reuses the production configuration (`docker-compose.yml` + `docker-compose.prod.yml`) and
adds `docker-compose.contabo.yml` (per-environment overlay), `infrastructure/gateway/` (shared
gateway), `infrastructure/caddy/Caddyfile.origin` (per-environment origin) and
`infrastructure/scripts/{gateway.sh,check-env-isolation.py}`. Background:
[deployment.md](deployment.md), [live-streaming/README.md](live-streaming/README.md).

**First deployment with production untouched:** follow
[the final UAT preparation](readiness/2026-11-03-uat/REPORT.md). The existing
`/opt/esabong` is already a checkout: preserve it and clone UAT and the gateway into
new child directories only after deployment approval. The gateway template now selects
`GATEWAY_MODE=uat-only`: only UAT has a TLS site or upstream, and the gateway joins no
production network. `GATEWAY_MODE=dual` enables the full routing described below and
requires separate production approval. Existing gateway env files without this setting
retain dual mode; set the mode explicitly before any first UAT deployment.

> **Approval gates.** Nothing in this repository deploys by itself. Migrations run only through
> `infrastructure/scripts/deploy.sh` with an approval that **names the environment**:
> `APPROVED_MIGRATIONS=uat:0016` can never migrate production, and vice versa (section 8).
> Never run a bare `docker compose up -d` after checking out a release with pending migrations:
> in this stack, starting a release **is** migrating it. Production is deployed only after UAT
> sign-off and an explicit production approval.

**Quick path for UAT** (details in the sections named): 1 server prep → 2 Docker → 3 checkouts →
4 env files → 5 DNS → 6 gateway → **7 deploy UAT** → 10 smoke tests. Production follows in
section 8 once UAT is signed off.

---

## 0. Layout and isolation

```
                    Internet  :80 / :443                  :1935 :8189   (prod media)
                        │                                  :1945 :8190   (UAT media)
          ┌─────────────▼──────────────┐                     │
          │ esabong-gateway (Caddy)    │  TLS, Host routing,  │
          │  swcmain183.live      ─────┼──┐ UAT access gate   │
          │  uat.swcmain183.live  ───┐ │  │                   │
          └──────────────────────────┼─┼──┼───────────────────┘
        esabong-uat-gateway (internal)│ │  │esabong-prod-gateway (internal)
          ┌──────────────────────────▼┐ │ ┌▼───────────────────────────────┐
          │ esabong-uat  (/opt/…/uat) │ │ │ esabong-prod (/opt/esabong/prod)│
          │ origin → api, worker,     │ │ │ origin → api, worker, beat,     │
          │ beat, mediamtx            │ │ │ mediamtx                        │
          │ postgres, redis (internal)│ │ │ postgres, redis (internal)      │
          └───────────────────────────┘ │ └─────────────────────────────────┘
                                        └─ other hosts (tenant subdomains, verified custom
                                           domains) → production only, on-demand TLS
```

| Resource | Production | UAT | How it is kept apart |
|---|---|---|---|
| Checkout / `.env` | `/opt/esabong/prod/.env` | `/opt/esabong/uat/.env` | separate Git checkouts; `deploy.sh` requires directory name = `ESB_STACK` |
| Compose project | `esabong-prod` | `esabong-uat` | `name: esabong-${ESB_STACK}` in `docker-compose.contabo.yml` |
| PostgreSQL, Redis | own containers + volumes `esabong-prod_*` | `esabong-uat_*` | each on its project's **internal** `backend` network; no host port |
| API, worker, beat, migrate | own containers | own containers | connect only to their own `postgres` / `redis` service names (checked) |
| Secrets | own values | own values | `check-env-isolation.py` fails on any shared secret |
| Portals / media files | own origin + build + `media` volume | own | each origin serves its own image and volume |
| MediaMTX | RTMP 1935, RTMPS 1936 (loopback), WebRTC 8189 | 1945, 1946 (loopback), 8190 | distinct host **and** in-container WebRTC port |
| Gateway link | `esabong-prod-gateway` | `esabong-uat-gateway` | internal networks; only the gateway joins both |
| Certificates | gateway volume `esabong-gateway_gatewaydata` | same | the gateway holds no application data |
| CPU / memory | weight 1024, OOM 0, ~7.25 GiB caps | weight 256, OOM +500, ~2.75 GiB caps | section 13 |

**Routing and tenants.** The gateway routes by Host only and forwards the Host unchanged. Each
API resolves tenants exactly as before, plus one new setting, `ROOT_TENANT_SLUG`: the bare
`BASE_DOMAIN` (e.g. `swcmain183.live`) serves that one tenant. Tenant subdomains and verified
custom domains are unchanged. `PLATFORM_HOST` equals `BASE_DOMAIN`, and the owner console is
served at `/owner/` on that host only. The owner console talks only to `/api/v1/platform/*`,
which uses its own token type and refresh cookie (`esb_prt`, path `/api/v1/platform/auth`):
a tenant token is refused by the platform API and a platform token by tenant endpoints (both
covered by `api/tests/test_root_tenant.py`). Admin and player keep their separate cookies
(`esb_rt_admin`, `esb_rt_player`). Cookies are host-only, so production cookies are never sent
to UAT. The trade-off: owner and tenant portals now share an origin, which they did not before.
The strict CSP (`script-src 'self'`) and HttpOnly path-scoped cookies limit what a portal bug
could reach. For defense in depth, restrict `/owner/` and `/api/v1/platform/*` to known
addresses with `PROD_OWNER_ALLOWED_IPS` (section 6).

**Client addresses.** The gateway trusts no proxy, so a client cannot forge `X-Forwarded-For`.
Each origin trusts only the gateway (a private Docker address), so the API's rate limits, audit
log and MediaMTX see the real client address.

---

## 1. VPS initialization and SSH security

Order the VPS with **Ubuntu 24.04** and your SSH public key (or the root password Contabo emails).

```bash
# Workstation → first login
ssh root@46.250.249.61

apt update && apt -y full-upgrade
timedatectl set-timezone UTC
hostnamectl set-hostname esabong-vps
apt -y install ufw fail2ban unattended-upgrades git curl ca-certificates python3 jq
dpkg-reconfigure -f noninteractive unattended-upgrades

# Deploy user with your workstation key (paste the key from ~/.ssh/id_ed25519.pub)
adduser --gecos "" deploy                 # set a strong password: sudo asks for it
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo 'ssh-ed25519 AAAA…your-key… you@workstation' > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys

# 4 GB swap as a safety net (RAM 12 GB; both environments + gateway are capped at ~10.25 GiB)
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-esabong.conf && sysctl --system >/dev/null
```

SSH hardening: **keep the root session open** until a new `deploy` login works.

```bash
cat > /etc/ssh/sshd_config.d/10-esabong.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers deploy
EOF
sshd -t && systemctl restart ssh
# In a NEW terminal on the workstation:  ssh deploy@46.250.249.61   (must work before you log out)
```

Host firewall (UFW). Docker-published ports bypass UFW by design; what is published is defined
by the Compose files and checked by preflight. UFW protects everything else, notably SSH.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit 22/tcp                       # or: sudo ufw allow from <your admin IP> to any port 22 proto tcp
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw allow 8189/udp && sudo ufw allow 8189/tcp     # production WebRTC media
sudo ufw allow 8190/udp && sudo ufw allow 8190/tcp     # UAT WebRTC media
sudo ufw allow 1935/tcp && sudo ufw allow 1945/tcp     # only if you use RTMP ingest (section 9)
sudo ufw enable && sudo ufw status verbose
sudo systemctl enable --now fail2ban        # sshd jail is on by default
```

## 2. Docker installation

Docker's official repository (not the Ubuntu `docker.io` package):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy              # log out and back in (members of "docker" are root-equivalent)
docker version && docker compose version    # Compose v2.24+ (needed for !reset / !override)
```

## 3. GitHub deployment workflow (two checkouts)

Pull-based: the server fetches tagged releases with a **read-only deploy key**.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_esabong -N "" -C "esabong-vps-deploy"
cat ~/.ssh/github_esabong.pub
# GitHub → richardfreyes/E-Sabong → Settings → Deploy keys → Add deploy key
#   title "esabong-vps", paste the key, leave "Allow write access" UNCHECKED
cat >> ~/.ssh/config <<'EOF'
Host github-esabong
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_esabong
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T github-esabong                       # "successfully authenticated … does not provide shell access"

sudo install -d -o deploy -g deploy /opt/esabong
git clone git@github-esabong:richardfreyes/E-Sabong.git /opt/esabong/prod
git clone git@github-esabong:richardfreyes/E-Sabong.git /opt/esabong/uat
install -d -m 700 /opt/esabong/prod/db/backups /opt/esabong/uat/db/backups   # preflight requires 700
```

The directory names **must** be `prod` and `uat`: `deploy.sh` refuses to run when the directory
does not match `ESB_STACK` in its `.env`. The gateway runs from the production checkout.

Releasing (on the workstation, **after the changes are approved and committed**):

```bash
git push origin production-readiness
git tag -a release-20261110-1 -m "Contabo release" && git push origin release-20261110-1
```

Every release goes to UAT first (`/opt/esabong/uat`), then, after sign-off, the **same tag** to
production (`/opt/esabong/prod`). UAT may run a newer tag than production; they share no code
or images (`esabong-uat-api` and `esabong-prod-api` are separate image names).

## 4. Environment files

Three files, all mode 600, git-ignored, never committed. Each environment gets **its own**
freshly generated secrets.

```bash
gen() { openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-40; }
fill_secrets() {   # fill_secrets <env file>
  for v in POSTGRES_SUPERUSER_PASSWORD DB_OWNER_PASSWORD DB_APP_PASSWORD DB_PLATFORM_PASSWORD REDIS_PASSWORD MEDIAMTX_API_PASSWORD; do
    sed -i "s|^$v=.*|$v=$(gen)|" "$1"
  done
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -base64 96 | tr -d '\n/+=' | cut -c1-64)|" "$1"
  FERNET=$(docker run --rm python:3.12-slim python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
  sed -i "s|^VIDEO_CREDENTIALS_KEY=.*|VIDEO_CREDENTIALS_KEY=$FERNET|" "$1"
}

# UAT
cd /opt/esabong/uat && cp .env.contabo.uat.example .env && chmod 600 .env && fill_secrets .env
nano .env    # ACME_EMAIL, SMTP_* (a test mailbox), MAIL_FROM
# Production
cd /opt/esabong/prod && cp .env.contabo.prod.example .env && chmod 600 .env && fill_secrets .env
nano .env    # ACME_EMAIL, SMTP_*, MAIL_FROM; ROOT_TENANT_SLUG later (section 8)
# Gateway (in the production checkout)
cd /opt/esabong/prod/infrastructure/gateway && cp .env.example .env && chmod 600 .env
sed -i "s|^UAT_ACCESS_TOKEN=.*|UAT_ACCESS_TOKEN=$(openssl rand -hex 24)|" .env
nano .env    # ACME_EMAIL, UAT_ALLOWED_IPS (testers' public IPs), PROD_OWNER_ALLOWED_IPS (optional)

grep -n '^[^#]*CHANGE_ME' /opt/esabong/{prod,uat}/.env /opt/esabong/prod/infrastructure/gateway/.env
cd /opt/esabong/prod && python3 infrastructure/scripts/check-env-isolation.py envs \
  /opt/esabong/prod/.env /opt/esabong/uat/.env --gateway-env infrastructure/gateway/.env
```

`check-env-isolation.py` must print only `PASS` lines (plus a `WARN` for production's empty
`ROOT_TENANT_SLUG` until section 8). It fails on shared secrets, equal hosts, port clashes, UAT
not capped below production, synthetic data in production, or memory limits that do not fit.

| Setting | Production | UAT | Why |
|---|---|---|---|
| `ESB_STACK` | `prod` | `uat` | project, volume and network names |
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml:docker-compose.contabo.yml` | same | production overlay + Contabo overlay |
| `ENVIRONMENT` | `production` | `production` | UAT runs the same startup safety checks |
| `BASE_DOMAIN` = `PLATFORM_HOST` | `swcmain183.live` | `uat.swcmain183.live` | owner at `/owner/` on this host |
| `ROOT_TENANT_SLUG` | the production tenant (section 8) | `uat-demo` | tenant served at `/` and `/admin/` |
| `SYNTHETIC_DATA_ENABLED` | `false` (enforced) | `true` | demo data commands and test-pattern streams |
| `STREAM_RTMP_PORT` / `STREAM_RTMPS_PORT` / `STREAM_WEBRTC_PORT` | 1935 / 1936 / 8189 | 1945 / 1946 / 8190 | no shared media port |
| `STREAM_PUBLIC_HOSTS` | `46.250.249.61` | `46.250.249.61` | address in WebRTC candidates |
| `CPU_SHARES` / `OOM_SCORE_ADJ` / `PIDS_LIMIT` | 1024 / 0 / 1024 | 256 / 500 / 512 | UAT yields CPU and is killed first under memory pressure |

Do not set `COMPOSE_PROJECT_NAME` and do not `export` any of these variables in your shell:
Compose lets the shell override `.env`, so `deploy.sh` and `gateway.sh` refuse to run when an
exported variable shadows a `.env` key. Back up all three files separately (password manager).

## 5. Domain and DNS

| Name | Type | Value | Notes |
|---|---|---|---|
| `swcmain183.live` | A | `46.250.249.61` | production |
| `uat.swcmain183.live` | A | `46.250.249.61` | UAT (already configured) |
| `*.swcmain183.live` (optional) | A | `46.250.249.61` | only for extra production tenants at `<slug>.swcmain183.live`; `uat` is a reserved slug |

Publish **A records only** (no AAAA) unless IPv6 is verified end to end: without Docker IPv6,
IPv6 clients reach the gateway through Docker's userland proxy and appear with a private
address, which the UAT allowlist (correctly) does not accept.

```bash
dig +short swcmain183.live && dig +short uat.swcmain183.live     # both: 46.250.249.61
dig +short AAAA swcmain183.live                                    # empty
```

## 6. Shared gateway and HTTPS

The gateway (`infrastructure/gateway/`, project `esabong-gateway`) is the only container on
80/443. In the template's `GATEWAY_MODE=uat-only`, it serves only UAT and joins only UAT's
private gateway link. The independent gateway checkout for this first deployment is described
in the final UAT preparation linked above. The commands below describe full dual mode.
In `GATEWAY_MODE=dual`, it obtains certificates for `swcmain183.live` and `uat.swcmain183.live` at the first
request (ports 80 and 443 must be reachable). Other hosts get a certificate only if
**production's** API approves them (tenant subdomain or verified custom domain). UAT has no
catch-all.

```bash
cd /opt/esabong/prod
infrastructure/scripts/gateway.sh networks     # creates esabong-prod-gateway and esabong-uat-gateway (internal)
infrastructure/scripts/gateway.sh validate     # .env + Caddyfile + UAT token/allowlist rules
# Start it once at least one environment is running (section 7); until then it answers 502.
```

**UAT access is restricted** at the gateway, before any request reaches UAT:

- Addresses in `UAT_ALLOWED_IPS` (space-separated public IPs/CIDRs) get in directly. The
  gateway refuses `0.0.0.0/0`, `::/0` and private or loopback ranges.
- Anyone else opens `https://uat.swcmain183.live/__uat/access` once per browser. The browser
  asks for a user name and password: `uat` / `<UAT_ACCESS_TOKEN>`. On success the gateway sets
  an HttpOnly, Secure, SameSite=Lax, 30-day cookie and redirects to `/`. Rotating
  `UAT_ACCESS_TOKEN` (+ `gateway.sh up`) revokes every browser.
- The token never appears in a URL. It travels only in that one request's `Authorization`
  header and then in the cookie. Both headers are deleted from the gateway's access log, and
  the gateway **removes the cookie before forwarding**, so UAT's origin, API and MediaMTX never
  receive it. Nothing leaks through browser history, bookmarks or `Referer` headers. The sign-in
  response is `no-store` and `Referrer-Policy: no-referrer`. Share the token through a password
  manager, never as a link. (An old-style `?key=` link is refused and its query is not logged.)
- Everything else gets `403 UAT access is restricted.` UAT sends `X-Robots-Tag: noindex` and a
  disallow-all `robots.txt`.
- Behind the gate, UAT still needs normal UAT accounts: the gate adds to the application's
  authentication and does not replace it.
- To let `esabong-monitor@uat` reach UAT, add the VPS's own address (`46.250.249.61/32`) to
  `UAT_ALLOWED_IPS`.

Optional production hardening: `PROD_OWNER_ALLOWED_IPS="<office IP>/32 <VPN egress>/32"`
limits `/owner/` and `/api/v1/platform/*` to those addresses (403 elsewhere). Tenant portals
are unaffected.

Changing the Caddyfile: `gateway.sh reload` (no dropped connections). Changing the gateway
`.env`: `gateway.sh up` (recreates the container: a few seconds without HTTPS for both
environments).

## 7. Deploying UAT (exact steps)

Prerequisites: sections 1–6 done; release tag `<release>` pushed; `check-env-isolation.py` all
PASS. All commands run as `deploy`. Nothing here touches `/opt/esabong/prod` except starting the
gateway.

```bash
cd /opt/esabong/uat
infrastructure/scripts/deploy.sh <release>
```

What it does, in order: checks `.env` (mode 600, `ESB_STACK=uat`, directory `uat`, Contabo
compose files, no shell overrides); requires the network `esabong-uat-gateway`; if
`/opt/esabong/prod/.env` is readable, runs the isolation check against it; checks out
`<release>`; `docker compose build --pull` (images `esabong-uat-*`); checks that the merged
config belongs to `esabong-uat` only (`check-env-isolation.py compose uat`); runs **preflight**
with the new image; starts **only UAT's** PostgreSQL and Redis; compares UAT's database revision
with the release. On a new server UAT's database is empty, so it stops:

```
    database: <empty>   release: 0016
STOPPED: migrations up to 0016 are pending in esabong-uat and not approved. Nothing was restarted.
After approval:  APPROVED_MIGRATIONS=uat:0016 infrastructure/scripts/deploy.sh <release>
```

After the UAT migration is approved:

```bash
APPROVED_MIGRATIONS=uat:0016 infrastructure/scripts/deploy.sh <release>     # migrates UAT only, starts UAT

# Plans/feature flags, the UAT owner account, synthetic data (UAT only; refused in production)
docker compose run --rm api python -m app.cli seed
docker compose run --rm api python -m app.cli create-platform-admin --email <you>@<your-domain> --name "UAT Owner"
docker compose run --rm api python -m app.cli demo-tenant --slug uat-demo --password '<strong UAT test password>'
docker compose run --rm api python -m app.cli demo-derby --slug uat-demo --fights 40

# Gateway (first time; from the production checkout)
cd /opt/esabong/prod && infrastructure/scripts/gateway.sh up
```

`demo-tenant` creates the `uat-demo` tenant with synthetic accounts (`admin`, `operator`,
`declarator`, `master1`, `agent1`, `player1..3`, all with the password you gave). It refuses the
documented development password in production mode. All balances start at zero, and play
balances are non-monetary. **Never load a production dump into UAT.**

UAT is now at `https://uat.swcmain183.live/` (player), `/admin/` (log in as `admin`), and
`/owner/` (the UAT owner account). Run the smoke tests in section 10 against UAT.

Updating UAT later: `cd /opt/esabong/uat && infrastructure/scripts/deploy.sh <new release>`.
It stops again if the release brings a migration; approve it with `APPROVED_MIGRATIONS=uat:<rev>`.
UAT's own backup is taken and verified first.

## 8. Deploying production (after UAT sign-off)

Only with explicit approval to deploy production **and** to run its migration. Same release tag
UAT signed off.

After that approval, set `GATEWAY_MODE=dual` in the gateway's own `.env` and run its
`gateway.sh up` from the gateway checkout. This attaches the production network and
enables the production hostname and on-demand tenant TLS. UAT-only mode deliberately
does not expose production even if production containers exist.

```bash
cd /opt/esabong/prod
infrastructure/scripts/deploy.sh <release>                       # builds esabong-prod-*, preflight, STOPS (empty DB)
APPROVED_MIGRATIONS=prod:0016 infrastructure/scripts/deploy.sh <release>    # after approval
docker compose run --rm api python -m app.cli seed
docker compose run --rm api python -m app.cli create-platform-admin --email <you>@<your-domain> --name "Owner"
```

Then make the root domain serve the production tenant:

1. Sign in at `https://swcmain183.live/owner/` with the temporary password printed above,
   approve plan prices (Owner → Plans), create the tenant (Owner → Tenants → New tenant), e.g.
   slug `swc-main`. Store the tenant admin's temporary password.
2. `sed -i 's|^ROOT_TENANT_SLUG=.*|ROOT_TENANT_SLUG=swc-main|' .env`
3. `infrastructure/scripts/deploy.sh <release>`: no pending migration, so it restarts the
   release with the new setting and checks health.
4. `https://swcmain183.live/` now shows that tenant's player portal and `/admin/` its admin
   portal (the tenant is also reachable at `https://swc-main.swcmain183.live/` if wildcard DNS
   exists).

Until step 3, `https://swcmain183.live/` answers "no tenant" while `/owner/` already works.

### How migrations stay isolated

- `migrate` runs in the environment's own project and connects to `postgres:5432` on that
  project's internal `backend` network. That name resolves only to the environment's own
  database. `check-env-isolation.py compose` fails the deploy if a database or Redis URL points
  anywhere else.
- The approval must name the environment (`uat:<rev>` / `prod:<rev>`), and `deploy.sh` must run
  from the matching directory with the matching `ESB_STACK`. An approval given for UAT cannot
  migrate production.
- Before migrating an existing database, `deploy.sh` backs up **that environment's** database
  into **its own** `db/backups/` and restores the dump into a scratch database to verify it.
- UAT migrations use UAT's images (`esabong-uat-api`). Production images and containers are not
  rebuilt, restarted or migrated by a UAT deploy (verified, section 14).

### Upgrading an existing database / moving data

Same as before, per environment: `deploy.sh <release>` stops and lists the pending revisions;
after approval `APPROVED_MIGRATIONS=<stack>:<rev> deploy.sh <release>`. `db/scripts/restore.sh`
restores **and migrates** with the currently built image of the checkout you run it in, so run
it only in the intended environment's directory, as part of an approved step. Never restore a
production dump into UAT (UAT uses synthetic data only). `restore.sh` enforces this: it refuses to
run outside `/opt/esabong/<ESB_STACK>`, refuses a dump whose name carries the other
environment's prefix (`prod-…` in UAT, `uat-…` in production), and asks you to type
`<stack>:<db>` (e.g. `uat:esabong`), not just the database name, which is the same in both.

What 0016 does: creates the empty `video_streams` table with forced row-level security. It reads
and changes no existing row. After any upgrade, users sign in once more in the browser portals.

## 9. MediaMTX and OBS (per environment)

Each environment runs its own MediaMTX (`infrastructure/mediamtx/mediamtx.yml`). Its control
API is reachable only by its own API, and auth callbacks go only to its own API.

| | Production | UAT |
|---|---|---|
| RTMP (OBS *Custom*) | `rtmp://46.250.249.61:1935/live` | `rtmp://46.250.249.61:1945/live` |
| WHIP (OBS 30+, recommended) | `https://swcmain183.live/stream/webrtc/live/<id>/whip` | `https://uat.swcmain183.live/stream/webrtc/live/<id>/whip` (UAT gate applies: run OBS from an allowlisted address) |
| WebRTC media | UDP/TCP 8189 | UDP/TCP 8190 |
| Limits | 2 CPU / 2 GiB | 0.5 CPU / 512 MiB |
| Test-pattern streams | off (enforced) | on (`STREAM_TEST_SOURCE=true`) |

Stream key format: `<id>?key=<publish key>` (Admin → Live streaming shows both). A UAT publish
key is unknown to production's API and vice versa, so a key never works on the other
environment's port.

```bash
cd /opt/esabong/<prod|uat> && docker compose ps mediamtx && docker compose logs --tail 20 mediamtx
# listeners: RTMP :1935 (container), WebRTC :8189 (prod) or :8190 (UAT), HLS :8888, API :9997
```

**RTMP allowlist per environment** (RTMP sends the key unencrypted; Docker ports bypass UFW):

```bash
# Production
printf 'RTMP_ALLOWED_SOURCES=<broadcaster IP>\nSTREAM_RTMP_PORT=1935\n' | sudo tee /opt/esabong/prod/.rtmp-allowlist.env >/dev/null
# UAT
printf 'RTMP_ALLOWED_SOURCES=<tester IP>\nSTREAM_RTMP_PORT=1945\n' | sudo tee /opt/esabong/uat/.rtmp-allowlist.env >/dev/null
sudo chmod 600 /opt/esabong/{prod,uat}/.rtmp-allowlist.env
sudo cp /opt/esabong/prod/infrastructure/systemd/esabong-rtmp-allowlist@.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now esabong-rtmp-allowlist@prod esabong-rtmp-allowlist@uat
sudo STREAM_RTMP_PORT=1935 /opt/esabong/prod/infrastructure/scripts/rtmp-allowlist.sh show
```

Each port has its own iptables chain (`ESABONG-RTMP-1935`, `ESABONG-RTMP-1945`), matched on the
connection's original destination port. Applying one environment's list never changes the
other's. To close RTMP entirely (WHIP only), set `STREAM_RTMP_BIND=127.0.0.1` in that
environment's `.env` and redeploy it. (Not testable on the macOS workstation: verify with `show`
on the server.)

**Bandwidth is shared.** Both environments use the VPS's single 300 Mbit/s port. Measured
capacity (section 14, 3 Nov) is about 75 viewers at 2.5 Mbps or 100 at 1.5 Mbps in total. Do not
run UAT streaming load tests during live production events. OBS settings for production are
unchanged: 1280×720/30, CBR 2,500 kbps (≤ ~75 viewers) or 1,500 kbps (≤ ~100), keyframe 1 s,
x264 veryfast/zerolatency, AAC 128 kbps, auto-reconnect on.

Celery **beat** must run in each environment (`video.reconcile` every 5 s):
`docker compose logs --tail 5 beat | grep video-reconcile`.

## 10. Smoke tests (per environment)

Replace `<host>` with `swcmain183.live` or `uat.swcmain183.live` and run from that
environment's directory. For UAT run the curl lines from an allowlisted address, or add
`-b esb_uat_access=<UAT_ACCESS_TOKEN>`.

```bash
docker compose ps                                              # all healthy; migrate "Exited (0)"
curl -fsS https://<host>/api/health && curl -fsS https://<host>/api/ready
curl -fsS https://<host>/api/v1/public/tenant | jq .slug       # the ROOT_TENANT_SLUG
for p in / /admin/ /owner/; do curl -s -o /dev/null -w "$p %{http_code}\n" https://<host>$p; done   # 200 each

# The repository's 25-check smoke test inside the API image (owner portal at /owner/, tenant on
# the root domain, sign-ins, WebSocket). For UAT add:  --cookie esb_uat_access=<UAT_ACCESS_TOKEN>
docker compose run --rm --no-deps -v "$PWD/api/scripts:/app/scripts:ro" \
  -e SMOKE_OWNER_PASSWORD='<owner password>' -e SMOKE_TENANT_PASSWORD='<smoke player password>' \
  api python scripts/smoke.py --platform https://<host> --tenant https://<host> --owner-portal-path /owner/ \
  --owner-email <owner email> --tenant-user <a dedicated smoke-test player>

# Once both environments run: no container of one can open a connection to the other.
infrastructure/scripts/probe-isolation.sh                      # every line "blocked", then PASS
```

Browser checks of the UAT gate and of the three portals on one origin (from a workstation with
Node; uses UAT's synthetic accounts):

```bash
cd web && E2E_CONTABO_URL=https://uat.swcmain183.live E2E_CONTABO_ACCESS_TOKEN='<UAT_ACCESS_TOKEN>' \
  E2E_CONTABO_OWNER_EMAIL='<UAT owner email>' E2E_CONTABO_OWNER_PASSWORD='<UAT owner password>' \
  E2E_CONTABO_TENANT_PASSWORD='<uat-demo password>' npx playwright test -c playwright.contabo.config.ts   # 6 passed
```

Isolation checks from the **workstation** (outside the server):

```bash
nmap -Pn -p 22,80,443,1935,1936,1945,1946,5432,6379,8888,8889,9997,9998 46.250.249.61
#   22, 80, 443 open; 1935/1945 open only if RTMP is enabled (filtered for non-allowlisted sources);
#   1936, 1946, 5432, 6379, 8888, 8889, 9997, 9998 closed/filtered
nmap -Pn -sU -p 8189,8190 46.250.249.61                                    # open|filtered
curl -s -o /dev/null -w '%{http_code}\n' https://uat.swcmain183.live/        # 403 from a non-allowlisted address
curl -s -o /dev/null -w '%{http_code}\n' https://swcmain183.live/api/v1/internal/tls-ask   # 404
curl -s -o /dev/null -w '%{http_code}\n' https://swcmain183.live/stream/webrtc/v3/paths/list  # 404
```

Manual checks (5 minutes per environment):

- **Sessions:** sign in to `/admin/` as an administrator and to `/` as a player in another tab,
  plus `/owner/` as the owner. Hard-refresh all three: each keeps its own account. Sign out of
  the player tab: admin and owner stay signed in.
- **Cross-environment:** a UAT token or cookie never works on production (different
  `JWT_SECRET`, host-only cookies). Signing in to UAT does not sign you out of production.
- **Streaming:** Admin → Live streaming, OBS connected → *Live*; *Test playback* shows
  LIVE · WebRTC; *Visible to players*; the player arena shows LIVE; declare a result: video keeps
  playing. In UAT, a *Test pattern* source works without OBS.

Roll back (section 11) if any check fails.

## 11. Backup and rollback (per environment)

Backups run in the environment's checkout and dump only its database into its own
`db/backups/`, named with the environment (`prod-esabong-<stamp>.dump`, `uat-esabong-<stamp>.dump`).
Production backups are mandatory. UAT backups are optional (synthetic data), but `deploy.sh`
always takes one before a UAT migration. `verify-backup.sh` compares every table's content,
except the live-stream telemetry columns of `video_streams` (status, viewers, check times),
which the reconcile task rewrites every 5 s.

```bash
cd /opt/esabong/prod && db/scripts/backup.sh && db/scripts/verify-backup.sh db/backups/prod-esabong-<stamp>.dump
# Off-site (production): /opt/esabong/prod/.offsite.env, mode 600, as in infrastructure/monitoring/README.md.
# OFFSITE_REMOTE must end with the environment, e.g. off:/srv/esabong-backups/prod (UAT: …/uat):
# retention deletes old files in that folder, so the two environments never share one.
sudo cp /opt/esabong/prod/infrastructure/systemd/esabong-{backup,monitor}@.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now esabong-backup@prod.timer esabong-monitor@prod.timer esabong-monitor@uat.timer
systemctl list-timers 'esabong-*'
```

The weekly restore drill (`esabong-restore-drill.service`) assumes `/opt/esabong`: copy it with
`WorkingDirectory`/`ExecStart` pointing at `/opt/esabong/prod` if you use it. Also back up the
gateway's certificate volume occasionally:
`docker run --rm -v esabong-gateway_gatewaydata:/d:ro -v "$PWD":/o alpine tar czf /o/gateway-data.tgz -C /d .`

**Rollback**, always in the affected environment's directory only:

| Situation | Action |
|---|---|
| Release without a new migration | `infrastructure/scripts/deploy.sh <previous release>` |
| Release with **0016** | 0016 only adds a table: `deploy.sh <previous release>` runs on a 0016 database. If the table itself must go: `docker compose run --rm migrate alembic downgrade 0015` (deletes stream configurations only) |
| Data damaged / other migrations | stop traffic; **first** `git checkout --detach <previous release> && docker compose build`; then `db/scripts/restore.sh db/backups/<stack>-esabong-<restore point>.dump [media-<stamp>.tar.gz]`; then `deploy.sh <previous release>`. Data after the backup is lost |
| Gateway change broke routing | `git -C /opt/esabong/prod checkout <previous release> -- infrastructure/gateway && infrastructure/scripts/gateway.sh reload` |

Never run `docker compose down -v` (deletes that environment's database volume). Never run
`docker volume prune` on this host.

## 12. Monitoring and troubleshooting

`infrastructure/monitoring/check.sh` checks one environment (its `.env`, containers, database,
disk, 5xx rate, certificates); the templated timers run one check per environment with
separate alert state (`/var/lib/esabong-monitor-<stack>`). Alerts: `ALERT_WEBHOOK_URL` and
`HEARTBEAT_URL` in `/opt/esabong/<stack>/.monitor.env` (mode 600). Name the environment in the
webhook (e.g. a separate channel for UAT).

```bash
cd /opt/esabong/<prod|uat>
docker compose ps                                     # this environment only
docker stats --no-stream                              # all containers; esabong-uat-* must stay under their caps
docker compose logs -f --tail 100 api
infrastructure/scripts/gateway.sh logs --tail 50      # gateway (both hosts; 403s from the UAT gate show here)
df -h / && docker system df
```

| Symptom | Likely cause → fix |
|---|---|
| `deploy.sh`: STOPPED | pending migration: get approval, rerun with `APPROVED_MIGRATIONS=<stack>:<rev>` |
| `deploy.sh`: "exported shell variables override .env" | `unset` the listed variables (or open a fresh shell) |
| `deploy.sh`: "ESB_STACK=… but this checkout is …" | you are in the wrong directory or copied the wrong `.env` |
| `deploy.sh`: "network esabong-<stack>-gateway missing" | `infrastructure/scripts/gateway.sh networks` (production checkout) |
| `502` on one host only | that environment's origin is down: `docker compose ps caddy api` in its directory |
| UAT `403 UAT access is restricted.` | your address is not in `UAT_ALLOWED_IPS` and no access cookie: open `/__uat/access`, sign in as `uat` |
| Gateway restarts, log says `UAT_ACCESS_TOKEN must be…` | token missing/placeholder/too short, or a forbidden range in `UAT_ALLOWED_IPS` |
| `https://swcmain183.live/` says no tenant | `ROOT_TENANT_SLUG` empty or misspelled (section 8) |
| Certificate not issued | DNS, port 80/443 reachability, or (other hosts) production's API does not know the host |
| Players stuck on *Live · HLS* | that environment's WebRTC port (8189 / 8190) blocked, or `STREAM_PUBLIC_HOSTS` wrong |
| Production slow while UAT is busy | `docker stats`: UAT is capped (CPU weight 256, memory caps); stop UAT (`cd /opt/esabong/uat && docker compose stop`) if needed. Production is unaffected by stopping UAT |

## 13. Resource limits (12 GB / 6 vCPU)

| Service | Production CPU / memory | UAT CPU / memory |
|---|---|---|
| api (`API_WORKERS`) | 2 / 1536 MiB (4 workers) | 1 / 640 MiB (2 workers) |
| worker (`WORKER_CONCURRENCY`) | 1 / 768 MiB (2) | 0.5 / 384 MiB (1) |
| beat | 0.5 / 256 MiB | 0.5 / 256 MiB |
| postgres (`shared_buffers`, `max_connections`) | 2 / 2 GiB (512 MB, 200) | 1 / 640 MiB (128 MB, 60) |
| redis (`maxmemory`) | 1 / 512 MiB (256 MB) | 0.5 / 256 MiB (128 MB) |
| origin (Caddy) | 0.5 / 256 MiB | 0.25 / 128 MiB |
| mediamtx | 2 / 2 GiB | 0.5 / 512 MiB |
| **Total** | **≈ 7.25 GiB** | **≈ 2.75 GiB** |
| gateway | 1 / 256 MiB | |

Totals: 10.25 GiB of memory caps on a 12 GB host (~1.75 GiB left for the OS and page cache;
`check-env-isolation.py` recomputes it from the `.env` files). Beyond hard caps, UAT has a CPU
weight of 256 vs 1024: when both compete, production gets 4× the CPU time. UAT also has
`oom_score_adj=500`, so under host memory pressure the kernel kills UAT processes first. Each
container also has a process limit (`PIDS_LIMIT`) and rotated logs (UAT: 10 MB × 3). Disk is
shared: the monitor alerts at 85 %. Prune old UAT backups and images (`docker image prune`, not
`volume prune`) before production's.

## 14. Verification evidence

### 3 November 2026 — two-environment rehearsal (workstation, isolated)

The layout was rehearsed on the workstation with two real checkouts named `prod` and `uat`
(clones of a snapshot of this tree), freshly generated throwaway secrets, real image builds
and the real scripts. The gateway ran on ports 18080/18443 with Caddy's local CA
(`local_certs` added only for the rehearsal), so nothing contacted Let's Encrypt or the VPS.
Nothing ran against the developer stack (`esabong`) or any real server.

- **Deploy gate:** `deploy.sh` built `esabong-uat-*` and ran preflight (0 FAIL) and the compose
  ownership check (PASS), then STOPPED on the empty database. `APPROVED_MIGRATIONS=0016` and
  `APPROVED_MIGRATIONS=prod:0016` were both refused in UAT, and `uat:0016` was refused in
  production. `uat:0016` migrated UAT only and `prod:0016` production only. The script also
  refused an exported `ESB_STACK` and, until fixed, a `db/backups` with mode 755.
- **Production untouched by UAT:** after a full UAT redeploy, all 8 production containers had
  identical IDs, start times and images. With UAT stopped, production kept answering (UAT: 502).
  Production restarts are likewise confined to `esabong-prod`.
- **Data:** production DB held only `main-arena` and its owner; UAT only `uat-demo` and the UAT
  owner. Tokens across environments were refused in all directions (401), and UAT owner
  credentials do not sign in to production.
- **Routing (gateway → origin):** production `/`, `/admin/`, `/owner/` 200 (`<base href="/owner/">`,
  bundles served); `/owner` and `/admin` 308; HTTP→HTTPS 308; `/api/v1/internal/*` and the
  MediaMTX control API 404 on both hosts; unknown hosts get no certificate. `ROOT_TENANT_SLUG`:
  `https://swcmain183.live/` resolves `main-arena` (login URL `https://swcmain183.live/admin/`).
  Platform and tenant tokens are each refused by the other API. WebSocket upgrade 101 through
  both proxy hops.
- **UAT gate:** 403 without access; forged-empty cookie 403; the sign-in answers 401 without
  credentials, with a wrong password or user, and for an old `?key=` link. Correct credentials →
  303 + HttpOnly Secure cookie (`no-store`, `no-referrer`). Then 200 with `X-Robots-Tag: noindex`
  and a disallow-all robots.txt. A header echo behind the gateway received the other cookies but
  not the gate cookie. The token appeared in **0** gateway log lines (sign-in, cookie requests, a
  pasted `?key=` link). The entrypoint refuses missing, short,
  placeholder or regex-unsafe tokens and `0.0.0.0/0`, private and loopback allowlists.
  `PROD_OWNER_ALLOWED_IPS` → `/owner/` and platform API 403, tenant portals still 200.
- **Client address:** a spoofed `X-Forwarded-For`/`X-Real-IP` was not recorded. The audit log
  holds the address the gateway saw, never a gateway-internal one.
- **Streaming:** each API reaches only its own MediaMTX (`reachable: true`). UAT advertises
  RTMP `:1945` and WebRTC 8190 (also inside the container), production `:1935`/8189. A UAT
  test-pattern stream went live in about 5 s and its LL-HLS playlist played through the gateway.
  The same URL with the UAT token on the production host ended 401. Production refuses test
  patterns (`test_source_disabled`).
- **Limits applied by Docker:** exactly the table in section 13 (memory, CPUs, CPU shares
  1024/256, OOM 0/500, pids 1024/512). Idle usage: production ~0.85 GiB, UAT ~0.56 GiB.
- **Smoke test:** `api/scripts/smoke.py` 25/25 on production and 25/25 on UAT through the gateway.
- **Network isolation:** on the workstation (OrbStack) containers could reach other networks
  by IP: OrbStack routes between bridge networks. The same topology on a stock Docker Engine
  29.9 (Docker-in-Docker) blocked every cross-environment connection, including internal
  networks. **Run `infrastructure/scripts/probe-isolation.sh` on the VPS** (section 10). Even
  if a path existed, the environments share no credentials.

### 3 November 2026 — single-stack preparation (earlier the same day)

Recorded on the workstation in isolated environments. Raw data:
[`readiness/2026-11-03-contabo/`](readiness/2026-11-03-contabo/). This applies per environment,
since each environment uses the same images and limits as the single stack measured here.

- **Images and preflight:** production images built; preflight 0 FAIL, 0 WARN.
- **Backup/restore:** `backup.sh` + `verify-backup.sh` on the development database (41 tables,
  13,615 rows, 33 RLS policies, revision 0012): RESTORE VERIFIED.
- **Migration rehearsal** 0012→0015→0016→0015→0016 on the restored copy: 23 business tables
  (wallets, ledgers, points, bets, settlements, accounts, sessions, tenants, subscriptions,
  invoices, predictions, events, matches, results, declarations, audit) byte-identical.
- **Sign-in after migration** (14/14): admin and player stay independent, owner signs in,
  `X-Portal: admin` never widens a player token, replayed rotated token revokes the session.
- **Streaming capacity** (MediaMTX 2 CPU / 2 GiB, 300 Mbit/s cap, OBS-like feed):

  | Bitrate | Viewers | Server egress | RTP loss | Server CPU / RAM | Latency p50 / p95 |
  |---|---:|---:|---:|---:|---:|
  | 2.5 Mbps | 50 | 157 Mbit/s | 0 % | 29 % / 388 MiB | 500 / 517 ms |
  | 2.5 Mbps | 75 | 215 Mbit/s | 0 % | 35 % / 502 MiB | 535 / 568 ms |
  | 2.5 Mbps | 100 | 297 Mbit/s (cap) | 1.4 % | 53 % / 719 MiB | 2,049 / 2,432 ms |
  | 1.5 Mbps | 100 | 178 Mbit/s | 0 % | 34 % / 541 MiB | 523 / 556 ms |
- **Recovery with the real player:** broadcaster outage → picture again 5.4 s; MediaMTX restart
  → 5.6–6.7 s.

Not verified anywhere yet (needs the real VPS): Contabo throughput and routing, ACME issuance
for `swcmain183.live` / `uat.swcmain183.live`, SMTP delivery, OBS on the real uplink, the RTMP
iptables allowlist (Linux only), systemd units, IPv6, and the off-site target.
