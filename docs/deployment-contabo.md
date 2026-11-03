# Deploying E-Sabong on one Contabo Cloud VPS 6

Runbook for the whole platform (PostgreSQL, Redis, FastAPI, Celery worker + beat, Caddy with the
three Angular portals, MediaMTX live streaming) on **one** Contabo Cloud VPS 6: Ubuntu 24.04 LTS,
6 vCPU, 12 GB RAM, 100 GB NVMe, 300 Mbit/s port. It reuses the existing production configuration
(`docker-compose.yml` + `docker-compose.prod.yml`, `infrastructure/caddy/Caddyfile`,
`db/scripts/*`, `infrastructure/scripts/preflight.py`, `infrastructure/monitoring/check.sh`,
`infrastructure/systemd/*`); general background is in [deployment.md](deployment.md) and
[live-streaming/README.md](live-streaming/README.md).

> **Approval gates.** Nothing in this repository deploys by itself. Database migrations run only
> through `infrastructure/scripts/deploy.sh` with an explicit `APPROVED_MIGRATIONS=<revision>`
> (section 8). Do not run a bare `docker compose up -d` after checking out a release with pending
> migrations: in this stack, starting a release **is** migrating it.

Placeholders used below: `<VPS_IP>` (public IPv4 from Contabo), `<base>` (your domain, e.g.
`esabong.example`), `<slug>` (a tenant), `<release>` (a Git tag such as `release-20261110-1`).

**Verified before writing this runbook (3 November 2026, details in section 13):** production
images build; preflight passes with a Contabo-shaped `.env` (0 FAIL, 0 WARN); migration 0016
rehearsed on a restored copy of real-shaped data (all balances, ledgers, accounts and sessions
byte-identical; downgrade and re-upgrade clean); existing users sign in after the upgrade;
Admin/Player/Owner sessions stay independent; streaming measured at 5–100 viewers under a
300 Mbit/s cap with production resource limits; player recovery after outages measured.

---

## 1. VPS initialization and SSH security

Order the VPS with **Ubuntu 24.04** and your SSH public key (or the root password Contabo emails).

```bash
# Workstation → first login
ssh root@<VPS_IP>

apt update && apt -y full-upgrade
timedatectl set-timezone UTC
hostnamectl set-hostname esabong-prod
apt -y install ufw fail2ban unattended-upgrades git curl ca-certificates python3 jq
dpkg-reconfigure -f noninteractive unattended-upgrades

# Deploy user with your workstation key (paste the key from ~/.ssh/id_ed25519.pub)
adduser --gecos "" deploy                 # set a strong password: sudo asks for it
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo 'ssh-ed25519 AAAA…your-key… you@workstation' > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys

# 4 GB swap as a safety net (RAM is 12 GB; the containers are capped at ~7.5 GB)
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
# In a NEW terminal on the workstation:  ssh deploy@<VPS_IP>   (must work before you log out)
```

Host firewall (UFW). Docker-published ports (80, 443, 1935, 8189) bypass UFW by design; what is
published is controlled by the Compose files and checked by preflight (section 7). UFW protects
everything else on the host, notably SSH.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit 22/tcp                       # or: sudo ufw allow from <your admin IP> to any port 22 proto tcp
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw allow 8189/udp && sudo ufw allow 8189/tcp
sudo ufw allow 1935/tcp                     # only if you use RTMP ingest (section 9)
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
docker version && docker compose version
```

## 3. GitHub deployment workflow

Pull-based: the server fetches tagged releases with a **read-only deploy key**. No inbound
connection from GitHub, no CI secrets, no paid service.

On the server (as `deploy`):

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
git clone git@github-esabong:richardfreyes/E-Sabong.git /opt/esabong
```

Releasing (on the workstation, **after the changes are approved and committed**):

```bash
git push origin production-readiness
git tag -a release-20261110-1 -m "Contabo first release" && git push origin release-20261110-1
```

Deploying a release on the server: `infrastructure/scripts/deploy.sh <release>` (sections 7–8).
It refuses to run over local modifications, builds images, runs preflight, and **stops before
any pending migration** unless approved. History: `~/esabong-deploy-history.log`.

## 4. Environment variables

```bash
cd /opt/esabong
cp .env.contabo.example .env && chmod 600 .env

# Random secrets (no / + = characters, safe for URLs and sed)
for v in POSTGRES_SUPERUSER_PASSWORD DB_OWNER_PASSWORD DB_APP_PASSWORD DB_PLATFORM_PASSWORD REDIS_PASSWORD MEDIAMTX_API_PASSWORD; do
  sed -i "s|^$v=.*|$v=$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-40)|" .env
done
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -base64 96 | tr -d '\n/+=' | cut -c1-64)|" .env
FERNET=$(docker run --rm python:3.12-slim python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
sed -i "s|^VIDEO_CREDENTIALS_KEY=.*|VIDEO_CREDENTIALS_KEY=$FERNET|" .env

# Public IPv4 (Contabo assigns it directly to eth0)
IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1); echo "$IP"
sed -i "s|CHANGE_ME_PUBLIC_IPV4|$IP|g" .env

nano .env    # set BASE_DOMAIN, PLATFORM_HOST, ACME_EMAIL, SMTP_*, MAIL_FROM
grep -n CHANGE_ME .env && echo "^ still to fill in" || echo "no placeholders left"
```

| Setting | Value on the VPS | Why |
|---|---|---|
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml` | production overlay for every command and `db/scripts/*.sh` |
| `COMPOSE_PROFILES` | `streaming` | starts MediaMTX |
| `ENVIRONMENT` | `production` | API refuses to start with unsafe settings |
| `MEDIAMTX_API_PASSWORD` | 24+ random characters | API ↔ MediaMTX control API (never in a URL) |
| `STREAM_PUBLIC_HOSTS` | `<VPS_IP>` | the address browsers use for WebRTC media |
| `STREAM_RTMP_BIND` | `0.0.0.0`, or `127.0.0.1` for WHIP-only | plain RTMP sends the key unencrypted (section 9) |
| `MEDIAMTX_MEMORY` | `2g` | ≈ 1 GiB + 4 MiB × peak WebRTC viewers (measured) |

`.env`, `.offsite.env`, `.monitor.env` and `.rtmp-allowlist.env` stay mode 600 and are git-ignored.
Back up `.env` separately (password manager); without it the database cannot be opened.

## 5. Domain and DNS

At your DNS provider (TTL 300 while setting up):

| Name | Type | Value |
|---|---|---|
| `owner.<base>` | A | `<VPS_IP>` |
| `*.<base>` | A | `<VPS_IP>` (every tenant: `<slug>.<base>`, admin at `/admin/`) |
| `stream.<base>` (optional) | A | `<VPS_IP>` (a readable name for `STREAM_RTMP_URL`; no certificate needed) |

Publish **A records only** unless you have verified IPv6 end to end on this host. Send mail through
your provider's authenticated submission port (`SMTP_PORT=587`, `SMTP_USE_TLS=true`), not port 25.

```bash
dig +short owner.<base>  && dig +short test-tenant.<base>     # both must print <VPS_IP>
```

## 6. HTTPS with Caddy

Nothing to configure beyond `ACME_EMAIL`, `CADDY_TLS_MODE=acme` and DNS. Caddy obtains a public
certificate on the first TLS request for each host, but only for hosts the API approves
(`/api/v1/internal/tls-ask`: the owner host, existing tenant subdomains, verified custom domains).
Ports 80 and 443 must be reachable from the internet. After startup:

```bash
curl -sI https://owner.<base> | head -1                       # HTTP/2 200
docker compose logs caddy | grep -iE 'certificate obtained|error' | tail
```

Live video uses the same certificates: WHEP/WHIP signalling and LL-HLS are served on the tenant
host under `/stream/…`; WebRTC media is DTLS-encrypted on UDP/TCP 8189.

## 7. Docker Compose startup

```bash
cd /opt/esabong
git fetch --tags
infrastructure/scripts/deploy.sh <release>
```

What it does, in order: checks `.env` (mode 600, production overlay), checks out `<release>`,
`docker compose build --pull`, runs **preflight** (`infrastructure/scripts/preflight.py` with the
new API image: compose exposure, secrets, file modes, listening sockets), starts only PostgreSQL
and Redis, then compares the database revision with the release.

On a **new server** the database is empty, so it stops:

```
    database: <empty>   release: 0016
STOPPED: migrations up to 0016 are pending and not approved. Nothing was restarted.
After approval:  APPROVED_MIGRATIONS=0016 infrastructure/scripts/deploy.sh <release>
```

Expected preflight result: `PASS compose … only caddy 80/443 and mediamtx RTMP/WebRTC media
published; data services internal`, `PASS secrets …`, `PASS host …`, a `WARN oci …` (no Oracle CLI
here; ignore). Fix any `FAIL` before continuing.

## 8. Migration execution (after approval)

### First installation (empty database)

```bash
APPROVED_MIGRATIONS=0016 infrastructure/scripts/deploy.sh <release>
docker compose run --rm api python -m app.cli seed
docker compose run --rm api python -m app.cli create-platform-admin --email you@<base> --name "Owner"
```

`create-platform-admin` prints a temporary password. Sign in at `https://owner.<base>`, approve plan
prices (Owner → Plans), then create the first tenant (Owner → Tenants → New tenant).

### Upgrading an existing database (e.g. at 0012 or 0015)

```bash
infrastructure/scripts/deploy.sh <release>                       # stops, lists 0013…0016 or 0016
# after approval:
APPROVED_MIGRATIONS=0016 infrastructure/scripts/deploy.sh <release>
```

With approval the script runs `db/scripts/backup.sh` (kept, not pruned), proves the dump restores
(`db/scripts/verify-backup.sh` into a scratch database that is dropped afterwards), prints the
restore point, then starts the release; `migrate` applies the migrations before the API starts.

What 0016 does (rehearsed, section 13): creates the empty `video_streams` table with forced
row-level security and grants. It reads and changes **no** existing row. It depends on 0015.
All earlier gates still apply: 0013 keeps the retired points ledger read-only; populated
0014/0015 simulation tables must never be dropped.

After any upgrade, **users sign in once more** in the browser portals (each portal now keeps its
own session cookie; the old shared cookie is ignored). Accounts, passwords and balances are
unchanged.

### Moving an existing database onto the VPS (optional)

`db/scripts/restore.sh` loads the dump **and then runs `migrate`** for the release that is checked
out, and restarts the app. Restoring an older dump onto the new release is therefore itself a
migration: do it only as part of the approved step.

```bash
# Old host:  db/scripts/backup.sh   → copy db/backups/esabong-<stamp>.dump (+ .sha256, media-<stamp>.tar.gz)
scp db/backups/esabong-<stamp>.dump* db/backups/media-<stamp>.tar.gz deploy@<VPS_IP>:/opt/esabong/db/backups/
# VPS: deploy.sh <release> has built the release and STOPPED (empty database). After approval:
db/scripts/verify-backup.sh db/backups/esabong-<stamp>.dump          # the dump restores and matches
db/scripts/restore.sh db/backups/esabong-<stamp>.dump db/backups/media-<stamp>.tar.gz   # restores AND migrates to 0016
APPROVED_MIGRATIONS=0016 infrastructure/scripts/deploy.sh <release> # now at head: starts and checks health
```

## 9. MediaMTX and OBS configuration

MediaMTX runs from `infrastructure/mediamtx/mediamtx.yml` (2 CPU / 2 GiB limits, internal API,
RTSP loopback-only). Check it:

```bash
docker compose ps mediamtx                      # healthy
docker compose logs --tail 20 mediamtx          # listeners: RTMP :1935, WebRTC :8189 (UDP/TCP), HLS :8888, API :9997
```

Create streams in **Admin → Live streaming** (tenant admin, `https://<slug>.<base>/admin/`):
add a stream (source *OBS / encoder*), assign it to an event or *Default for every event*,
store the **publish key** (shown once), *Test playback*, then *Visible to players*.

**Ingest, most secure first:**

1. **WHIP over HTTPS (recommended, OBS 30+).** OBS → Settings → Stream → Service **WHIP**;
   Server `https://<slug>.<base>/stream/webrtc/live/<id>/whip`; Bearer Token = publish key.
   Encrypted, no extra port. Then close RTMP: `STREAM_RTMP_BIND=127.0.0.1` in `.env`,
   `docker compose up -d mediamtx`, `sudo ufw delete allow 1935/tcp`.
2. **RTMP from known addresses.** OBS Service *Custom*: Server `rtmp://<VPS_IP or stream.<base>>:1935/live`,
   Stream key `<id>?key=<publish key>`. RTMP is unencrypted, so allow 1935 only from the
   broadcaster (Docker ports bypass UFW, so this uses Docker's `DOCKER-USER` chain):

   ```bash
   echo 'RTMP_ALLOWED_SOURCES=<broadcaster IP>[,<another IP or CIDR>]' | sudo tee /opt/esabong/.rtmp-allowlist.env >/dev/null
   sudo chmod 600 /opt/esabong/.rtmp-allowlist.env
   sudo cp infrastructure/systemd/esabong-rtmp-allowlist.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now esabong-rtmp-allowlist.service
   sudo infrastructure/scripts/rtmp-allowlist.sh show
   ```
3. **RTMPS** needs a certificate mounted into MediaMTX (`STREAM_RTMP_ENCRYPTION=optional`,
   `STREAM_RTMPS_BIND=0.0.0.0`, `MTX_RTMPSERVERCERT`/`MTX_RTMPSERVERKEY`) and a MediaMTX restart
   after each renewal. Prefer WHIP.

**OBS output settings for this server** (300 Mbit/s shared by every viewer):

| Setting | Value |
|---|---|
| Resolution / FPS | 1280×720, 30 fps |
| Rate control | CBR **2,500 kbps** (≤ ~75 viewers) or **1,500 kbps** (≤ ~100 viewers) |
| Keyframe interval | 1 s |
| x264 | preset `veryfast`, tune `zerolatency`, profile main, no B-frames (or NVENC low-latency) |
| Audio | AAC 128 kbps (converted to Opus for WebRTC automatically) |
| Advanced → Automatically reconnect | on |

**Capacity on one VPS 6 (measured, section 13):** clean up to **75 viewers at 2.5 Mbps** (215
Mbit/s, 0 % loss, p95 0.57 s) and **100 viewers at 1.5 Mbps** (178 Mbit/s, 0 % loss). At 100 viewers
× 2.5 Mbps the 300 Mbit/s port saturates: 1.4 % loss and latency rises from ~0.5 s to ~2 s. CPU and
memory are not the limit (≤ 0.6 of a core, ≤ 0.75 GiB at 100 viewers). For larger audiences add a
CDN in front of LL-HLS or a second media server; one VPS cannot serve 1,000 viewers.

Celery **beat** must run (it does in this stack): every 5 s `video.reconcile` updates stream status
and viewer counts, re-pushes runtime configuration after a MediaMTX restart, disconnects viewers
whose access ended (stream hidden, session revoked), and tells players the moment a stream is live
again.

```bash
docker compose logs --tail 5 beat | grep video-reconcile
docker compose logs --tail 5 worker | grep 'video.reconcile.*succeeded'
```

## 10. Application smoke tests

```bash
cd /opt/esabong
docker compose ps                                              # all healthy; migrate "Exited (0)"
curl -fsS https://owner.<base>/api/health && curl -fsS https://owner.<base>/api/ready

# The repository's 24-check smoke test, run inside the API image (no host Python packages):
docker compose run --rm --no-deps -v "$PWD/api/scripts:/app/scripts:ro" \
  -e SMOKE_OWNER_PASSWORD='<owner password>' -e SMOKE_TENANT_PASSWORD='<smoke player password>' \
  api python scripts/smoke.py --platform https://owner.<base> --tenant https://<slug>.<base> \
  --owner-email <owner email> --tenant-user <a dedicated smoke-test player>

infrastructure/monitoring/check.sh && echo "monitor: healthy"
```

From the **workstation** (outside the server), exposure must look like this:

```bash
nmap -Pn -p 22,80,443,1935,1936,5432,6379,8888,8889,9997,9998 <VPS_IP>
#   22, 80, 443 open; 1935 open only if RTMP is enabled (filtered for non-allowlisted sources);
#   1936, 5432, 6379, 8888, 8889, 9997, 9998 closed/filtered
curl -s -o /dev/null -w '%{http_code}\n' https://<slug>.<base>/api/v1/internal/tls-ask      # 404
curl -s -o /dev/null -w '%{http_code}\n' https://<slug>.<base>/stream/webrtc/v3/paths/list  # 404
```

Manual checks (5 minutes):

- **Sessions:** in one browser, sign in to `https://<slug>.<base>/admin/` as an administrator and
  to `https://<slug>.<base>/` as a player in another tab; hard-refresh both: each keeps its own
  account; sign out of the player tab: the admin tab stays signed in.
- **Streaming:** in Admin → Live streaming, OBS connected → status *Live*; *Test playback* shows
  LIVE · WebRTC; enable *Visible to players*; the player arena shows LIVE within a few seconds;
  declare a result: the video keeps playing.
- **Owner:** `https://owner.<base>` sign-in works in the same browser.

Roll back (section 11) if any check fails.

## 11. Backup and rollback procedures

**Backups** (existing scripts, nightly with a weekly restore drill):

```bash
db/scripts/backup.sh                                  # dump + checksum + media → db/backups/ (prunes > 14 days)
db/scripts/verify-backup.sh db/backups/esabong-<stamp>.dump    # proves it restores
```

Off-site copy (`db/scripts/offsite-backup.sh`, rclone in a container, configured by
`/opt/esabong/.offsite.env`, mode 600). Any rclone remote works; without a paid service, use SFTP
to a machine you already own:

```bash
cat > .offsite.env <<'EOF'
OFFSITE_REMOTE=off:/srv/esabong-backups
RCLONE_CONFIG_OFF_TYPE=sftp
RCLONE_CONFIG_OFF_HOST=<backup host>
RCLONE_CONFIG_OFF_USER=<user>
RCLONE_CONFIG_OFF_KEY_PEM=<private key, newlines as \n>
OFFSITE_RETENTION_DAYS=35
EOF
chmod 600 .offsite.env
db/scripts/offsite-backup.sh                          # backup → verify → upload → download-back check
```

Timers (from `infrastructure/monitoring/README.md`):

```bash
sudo cp infrastructure/systemd/esabong-*.service infrastructure/systemd/esabong-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now esabong-monitor.timer esabong-backup.timer esabong-restore-drill.timer
systemctl list-timers 'esabong-*'
```

**Rollback**

| Situation | Action |
|---|---|
| Release without a new migration | `infrastructure/scripts/deploy.sh <previous release>` |
| Release with **0016** | 0016 only adds a table, so the previous release runs on a 0016 database: `infrastructure/scripts/deploy.sh <previous release>`. If the table itself must go: `docker compose run --rm migrate alembic downgrade 0015` (rehearsed; deletes stream configurations only) |
| Data damaged / other migrations | stop traffic; **first** `git checkout --detach <previous release> && docker compose build` (restore.sh runs `migrate` from the currently built image, so restoring while the new release's images exist would re-apply its migration); then `db/scripts/restore.sh db/backups/esabong-<restore point>.dump [media-<stamp>.tar.gz]` (the dump `deploy.sh` printed); then `infrastructure/scripts/deploy.sh <previous release>`. Data written after the backup is lost: announce a maintenance window |

Never run `docker compose down -v` (it deletes the database volume). After any rollback, repeat
section 10.

## 12. Basic monitoring and troubleshooting

`infrastructure/monitoring/check.sh` (every 5 minutes via the timer) checks health/readiness,
portals, WebSocket, containers, CPU/memory, disk, database size, 5xx rate, backups and
certificates. Alerts: put `ALERT_WEBHOOK_URL` (Discord/Slack/Teams webhook) and `HEARTBEAT_URL`
(e.g. a free healthchecks.io check) in `/opt/esabong/.monitor.env` (mode 600).

Day-to-day commands:

```bash
docker compose ps                                     # state and health
docker stats --no-stream                              # CPU / memory per container
docker compose logs -f --tail 100 api                 # JSON request logs
docker compose logs -f --tail 100 mediamtx            # one line per publisher/viewer session
docker compose logs api | grep mediamtx_auth | tail   # stream auth decisions (allowed, reason; never keys)
vnstat -l -i eth0   # (apt install vnstat) live bandwidth; keep video egress under ~210 Mbit/s
df -h / && docker system df
```

The Admin → Live streaming page also shows server reachability, live streams, WebRTC/HLS viewers
and outbound bandwidth.

| Symptom | Likely cause → fix |
|---|---|
| `deploy.sh` STOPPED | pending migration: get approval, rerun with `APPROVED_MIGRATIONS=<rev>` |
| API exits at start | `docker compose logs api` lists every unsafe setting (production checks) |
| Certificate not issued | DNS not pointing to `<VPS_IP>`, port 80/443 blocked, or the host is not a tenant/owner host |
| Admin page: *Streaming server unreachable* | `docker compose ps mediamtx`; same `MEDIAMTX_API_PASSWORD` for API and MediaMTX; after 20 failed API logins the API is locked out for 10 min |
| OBS: *Failed to connect* | key format `<id>?key=<key>`; key rotated?; 1935 closed or not allowlisted; use WHIP |
| Players stuck on *Live · HLS* | UDP/TCP 8189 blocked or `STREAM_PUBLIC_HOSTS` ≠ public IP |
| Video stalls with many viewers | port saturated: lower OBS bitrate (table in section 9) |
| Status never changes / viewers not disconnected after hiding a stream | `beat` or `worker` not running |
| Admin redirected or 403 after refresh | clear the site's cookies once and sign in again (pre-upgrade shared cookie) |

## 13. Verification evidence (3 November 2026)

Recorded on the workstation in isolated environments (no production server, no production
migration). Raw data: [`readiness/2026-11-03-contabo/`](readiness/2026-11-03-contabo/).

- **Images and preflight:** production API and Caddy images built from this tree (portals and
  stream routes included, migration 0016 present); preflight with a Contabo-shaped `.env` and the
  built image: 0 FAIL, 0 WARN; merged config publishes only Caddy 80/443 and MediaMTX 1935, 8189
  UDP/TCP (1936 on loopback); PostgreSQL/Redis unpublished on an internal network; memory limits
  ≈ 7.5 GiB of 12 GB.
- **Backup/restore:** `db/scripts/backup.sh` + `verify-backup.sh` on the development database
  (41 tables, 13,615 rows, 33 RLS policies, revision 0012): RESTORE VERIFIED.
- **Migration rehearsal** on that restored copy: 0012→0015 (adds simulation tables; only
  `role_permissions` gains the intended 101 rows), 0015→0016 (adds `video_streams`, no existing
  table changed), 0016→0015 (database identical to the earlier 0015 state), 0015→0016 again
  (identical). 23 business tables (wallets, ledgers, points, bets, settlements, accounts, sessions,
  tenants, subscriptions, invoices, predictions, events, matches, results, declarations, audit)
  byte-identical from 0012 to 0016.
- **Sign-in after migration** (API on the migrated copy, 14/14): existing admin and player sign in
  through their portals and stay independent; the legacy cookie is not reused by browser portals
  (one new sign-in); a player is refused by the admin portal; `X-Portal: admin` never widens a
  player token (403); logout is isolated; a replayed rotated token from another client revokes the
  session; the owner signs in.
- **Streaming capacity** (MediaMTX with the production limits 2 CPU / 2 GiB and a 300 Mbit/s egress
  cap; OBS-like RTMP feed: x264 veryfast/zerolatency CBR + AAC 128 kbps, 1 s keyframes; WebRTC
  viewers in a container on the same network; one Chromium viewer measuring glass-to-glass
  latency during each 60 s hold):

  | Bitrate | Viewers | Joined | Per viewer | Server egress | RTP loss | Server CPU / RAM | Latency p50 / p95 |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | 2.5 Mbps | 5 | 5 | 2.66 Mbps | 16 Mbit/s | 0 % | 9 % / 93 MiB | 499 / 549 ms |
  | 2.5 Mbps | 10 | 10 | 2.65 Mbps | 33 Mbit/s | 0 % | 12 % / 150 MiB | 516 / 533 ms |
  | 2.5 Mbps | 25 | 25 | 2.65 Mbps | 79 Mbit/s | 0 % | 20 % / 215 MiB | 522 / 540 ms |
  | 2.5 Mbps | 50 | 50 | 2.65 Mbps | 157 Mbit/s | 0 % | 29 % / 388 MiB | 500 / 517 ms |
  | 2.5 Mbps | 75 | 75 | 2.66 Mbps | 215 Mbit/s | 0 % | 35 % / 502 MiB | 535 / 568 ms |
  | 2.5 Mbps | 100 | 100 | 2.43 Mbps | 297 Mbit/s (cap) | 1.4 % | 53 % / 719 MiB | 2,049 / 2,432 ms |
  | 1.5 Mbps | 100 | 100 | 1.65 Mbps | 178 Mbit/s | 0 % | 34 % / 541 MiB | 523 / 556 ms |

  (CPU: 100 % = one core. Latency includes the OBS-like re-encode; WebRTC delivery itself measured
  42–59 ms in [live-streaming/README.md](live-streaming/README.md#test-results). Every viewer joined
  at every level; first packet 225–258 ms.)
- **Recovery with the real player** (two runs each): broadcaster offline 5 s → picture again
  **5.4 s** after the broadcaster returned; MediaMTX restart → picture again after **5.6–6.7 s**;
  one reconnect attempt each (no reconnect storms).
- **Test suites:** see `docs/ai-handoff/PROGRESS.md` §4 for the latest counts.

Not verified here (needs the real VPS): Contabo's actual port throughput and routing, ACME
issuance for your domain, SMTP delivery, OBS on your camera/uplink, IPv6, and the off-site target.
