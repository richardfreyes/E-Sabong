# Origin IP change and hiding (2026-11-20 DDoS)

The old origin IP was attacked directly twice on 2026-11-20 (05:59 and ~10:20 UTC) and Contabo
suspended it to protect its network. Cloudflare only protects traffic that goes **through**
Cloudflare; the attackers used the raw IP. It was known from DNS history (A records before the
cutover) and from every WebRTC answer (`STREAM_PUBLIC_HOSTS`). A new IP only helps if nothing
reveals it again.

**Never write the new IP into git, docs, chat or tickets other than Contabo's.** Use
`<new-ip>` in notes.

## What reveals an origin IP, and the control for each

| Leak | Control |
|------|---------|
| DNS records pointing at the IP (now or in history) | Cloudflare Tunnel: hostnames are CNAMEs to the tunnel; no A record holds the IP |
| Scanners (Censys/Shodan) reading the site certificate on 80/443 | Gateway ports bound to `127.0.0.1`; visitors arrive only through the tunnel |
| WebRTC answers to players (ICE candidates) | `STREAM_PUBLIC_WEBRTC=false`: players and other tenants' staff get LL-HLS through Cloudflare; MediaMTX refuses their WebRTC reads |
| RTMPS ingest certificate on 1936 | `STREAM_RTMPS_BIND=127.0.0.1` (operators publish from the browser, WHIP); open it only to known operator IPs if OBS is needed |
| Custom-domain instructions shown to tenant admins | `CUSTOM_DOMAIN_GATEWAY_IPS` lists only Cloudflare addresses, never the origin |
| SSH | Allow only admin IPs (host firewall / Contabo) |

## Prepared (2026-11-20, approved: tunnel creation and preparation only)

- **Tunnel** `esabong-origin`, ID `b10b0b23-e4b0-445d-af1a-a5aa6b8cc697`, remotely managed.
  Status `inactive` (no connector yet). **No DNS record points to it**; live traffic is unchanged.
- **Ingress (tunnel config v1)**, every rule `https://gateway:443`, No TLS Verify, SNI and Host set:

  | Public hostname | SNI / Host sent to the gateway | Tenant |
  |---|---|---|
  | `swcmain183.live` | `swcmain183.live` | `swc-main-183` (stream provider) |
  | `www.swcmain183.live` | `swcmain183.live` | same site (the gateway has no `www` certificate; add a www → apex redirect rule at cutover) |
  | `uat.swcmain183.live` | `uat.swcmain183.live` | UAT |
  | `swcmain185.live` | `swcmain185.live` | `swc-main-185` |
  | `www.swcmain185.live` | `swcmain185.live` | same site |
  | anything else | — | 404 |
- **Gateway path validated** without the token or DNS (a container on the gateway network
  sending exactly what cloudflared sends): `/api/ready` 200, SPA 200, `/api/v1/public/tenant`
  200 with the right tenant (`swc-main-183`, UAT), **WebSocket upgrade 101** on every hostname.
  `swcmain185.live` tenant lookup 404 is the separate domain re-check issue below, identical on
  today's path.
- **Secrets:** the tunnel token is never read through the API, chat, git or docs. The owner
  copies it from the dashboard (Zero Trust → Networks → Tunnels → `esabong-origin` → Configure →
  install command, the long string after `--token`) and writes it on the server with
  ```
  ssh -t deploy@<server> '~/esb-backup/put-secret.sh /opt/esabong/gateway/infrastructure/gateway/.tunnel.env TUNNEL_TOKEN'
  ```
  (input hidden, file mode 600, git-ignored; the compose `tunnel` service reads it via `env_file`).
- **Exposure check:** `infrastructure/scripts/origin-exposure-check.sh` (run from outside;
  reads the address from `~/.esabong-origin-ip`, mode 600, never prints it). Against the old IP
  today: DNS clean (www fixed), ports 80/443/1936/8189/8190 open and a `CN=swcmain183.live`
  certificate on 1936 → EXPOSED, as expected before migration.

### Connector readiness test (after the token is written; no DNS, no gateway restart)

```
docker run -d --name esb-tunnel-test --restart no --network esabong-gateway_public \
  --env-file /opt/esabong/gateway/infrastructure/gateway/.tunnel.env \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  cloudflare/cloudflared:2026.10.7 tunnel --no-autoupdate run
```
The tunnel then shows **healthy** with connections in the dashboard/API; with no DNS route no
visitor reaches it. Remove with `docker rm -f esb-tunnel-test` (the permanent connector is the
compose `tunnel` service at cutover). While attached to the `public` network the gateway sees
cloudflared's Docker address as the client; the cutover uses the `tunnel` network
(10.250.250.0/29), which the gateway trusts for CF-Connecting-IP.

### Cutover risk: custom-domain re-check

The hourly re-check (`domains.recheck_pending`, :15) failed `swcmain185.live` at 15:15 UTC
while the origin was suspended (its DNS lookups failed), and the tenant answered "No tenant is
configured for this host" until the next successful re-check (or an owner pressing **Verify**).
At cutover `CUSTOM_DOMAIN_GATEWAY_IPS` must list exactly the addresses `swcmain185.live`
resolves to through the tunnel (no origin IP); check `dig` before and after and press Verify.
Code follow-up: do not fail a verified domain on a lookup error (only on a definite mismatch,
or after several consecutive failures).

## Steps

Audit and priorities: [ddos-hardening-2026-11-20.md](ddos-hardening-2026-11-20.md).

0. **Off-server backup first.** Copy the latest verified prod and UAT dumps (and their
   `.sha256`) off the VPS before touching the network.
1. **New IP.** Order an additional IPv4 for the VPS (Contabo panel). Configure it on the
   interface as the **primary** address with the default route using it as source (Contabo's
   additional-IP guide, netplan), so outbound traffic (the tunnel) never leaves from the
   suspended address. Keep the old IP configured until step 6 so SSH still works when it is up.
   IPv6: publish no AAAA for the origin; remove the public IPv6 from `eth0` or firewall it like
   IPv4 (the tunnel needs no inbound IPv6).
2. **Tunnel (Cloudflare dashboard).** Zero Trust → Networks → Tunnels → Create (cloudflared),
   name `esabong-origin`, copy the token. Public hostnames, each with service
   `HTTPS` → `gateway:443`, TLS: *Origin Server Name* = the hostname, *No TLS Verify* = on:
   `swcmain183.live`, `www.swcmain183.live` (if used), `uat.swcmain183.live`, `swcmain185.live`,
   `www.swcmain185.live`. Delete each hostname's existing A/CNAME record first (the dashboard
   creates the tunnel CNAME). The `www` records are DNS-only today and resolve to the origin IP:
   they must never be grey again.
3. **Gateway**: move `/opt/esabong/gateway` to the release with the tunnel profile (it is on
   `uat-20261103-rc.1`), then set `/opt/esabong/gateway/infrastructure/gateway/.env` (mode 600) and run
   `infrastructure/scripts/gateway.sh up`:
   ```
   COMPOSE_PROFILES=tunnel
   CLOUDFLARE_TUNNEL_TOKEN=<token>
   HTTP_PORT=127.0.0.1:80
   HTTPS_PORT=127.0.0.1:443
   GATEWAY_TRUSTED_PROXIES=10.250.250.0/29
   GATEWAY_CLIENT_IP_HEADER=CF-Connecting-IP
   ```
   The gateway must run in dual mode (the uat-only overlay does not attach the tunnel network).
4. **Prod and UAT** (`/opt/esabong/{prod,uat}/.env`), then
   `docker compose up -d api worker mediamtx` in each:
   ```
   STREAM_PUBLIC_WEBRTC=false
   STREAM_PUBLIC_HOSTS=<new-ip>          # only operators/broadcaster staff ever receive it
   STREAM_RTMPS_BIND=127.0.0.1
   CUSTOM_DOMAIN_GATEWAY_IPS=<Cloudflare addresses swcmain185.live resolves to, no origin IP>
   ```
5. **Verify.**
   - `https://swcmain183.live/api/ready`, UAT and `swcmain185.live` answer through Cloudflare.
   - `curl -k --connect-timeout 5 https://<new-ip>/` from outside: refused / timeout.
   - `dig +short swcmain183.live` (and the other hostnames) return only Cloudflare addresses.
   - A player's playback response has `"url": ""` and plays "Live · HLS"; the provider's
     operator/declarator still gets WebRTC.
   - Betting, settlement, WebSocket updates and the declarator desk work on UAT before prod.
6. **Release the old IP** in the Contabo panel once everything runs on the new one, and remove it
   from the interface.

## MediaMTX: what Cloudflare can and cannot carry

| Service | Port | Through tunnel/proxy | DDoS protection needed |
|---|---|---|---|
| LL-HLS playback | 443 (`/stream/hls/`) | yes | Cloudflare (HTTP) |
| WHEP/WHIP signalling | 443 (`/stream/webrtc/`) | yes | Cloudflare (HTTP) |
| **WebRTC media** | **UDP/TCP 8189 (prod), 8190 (UAT)** | **no** | **separate TCP/UDP mitigation**: media VPS (sacrificial address) or a provider with L3/L4 protection; Cloudflare Spectrum for arbitrary UDP is Enterprise |
| **RTMPS ingest** | **TCP 1936** | **no** | **separate TCP mitigation** (Spectrum, paid) or allowlist the operators' IPs; close it otherwise |
| RTMP ingest | TCP 1935/1945/1946 | no | already bound to 127.0.0.1 |
| MediaMTX API / metrics | 9997 etc. | — | internal only (verified not published) |

## Encrypted off-site backups

- `db/scripts/offsite-backup.sh` now encrypts every object with GnuPG AES-256 before upload
  (`OFFSITE_PASSPHRASE_FILE`), downloads it back, **decrypts and checks the dump's SHA-256**,
  and on Sundays (`offsite-scheduled.sh`) restores the decrypted off-site copy into a scratch DB
  and compares it with live (row counts, content hashes, RLS, grants, triggers, revision).
- Installed on the server under `~/esb-backup/` (outside the release checkouts; deploys never
  change it): `offsite-backup.sh`, `offsite-scheduled.sh`, `put-secret.sh`, `backup.passphrase`
  (generated on the server, 65 bytes, mode 600; **copy it into the password manager from your
  own terminal**: without it no off-site backup can be decrypted).
- **UAT drill passed 2026-11-20 15:45 UTC** (local stand-in remote): only `*.gpg` + `.sha256`
  stored; off-site copy decrypted, matched, restored and verified.
- **To activate** (prerequisites: R2 enabled in the dashboard; an R2 API token with Object
  Read & Write on one private bucket `esabong-backups`):
  1. `~/esb-backup/{prod,uat}.offsite.env` (mode 600) with the R2 settings from the header of
     `offsite-backup.sh`, `OFFSITE_REMOTE=r2:esabong-backups/<env>`; write the key id and secret
     with `put-secret.sh`.
  2. One manual run per environment, then `crontab -e` (server time is UTC+8):
     ```
     30 3 * * * $HOME/esb-backup/offsite-scheduled.sh uat  >> $HOME/esb-jobs/offsite-uat.log 2>&1
     0  4 * * * $HOME/esb-backup/offsite-scheduled.sh prod >> $HOME/esb-jobs/offsite-prod.log 2>&1
     ```

## Rollback

Before the DNS cutover nothing is live: delete the tunnel (or leave it inactive) and remove
`.tunnel.env`. After it, each layer reverts on its own: tunnel hostnames back to proxied A records (Cloudflare DNS),
`COMPOSE_PROFILES=` and the original `HTTP_PORT`/`HTTPS_PORT` in the gateway `.env`
(`gateway.sh up`), `STREAM_PUBLIC_WEBRTC=true` (`docker compose up -d api worker mediamtx`).
Rolling back the tunnel or ports exposes the IP again.

## Later

Move MediaMTX to a separate small VPS so an attack on media only interrupts video, and players
can return to WebRTC there without exposing the application origin.

## Cutover readiness (prepared 2026-11-20; every ⛔ step needs the owner's explicit approval)

### Tenant routing (verified 2026-11-20 through the gateway path the tunnel uses)

| Hostname | Tenant | Check |
|---|---|---|
| `swcmain183.live`, `www.swcmain183.live` | `swc-main-183` (stream provider) | `/api/v1/public/tenant` 200 |
| `swcmain185.live`, `www.swcmain185.live` | `swc-main-185` | 200 after re-verification (below) |
| `uat.swcmain183.live` | UAT (`uat-demo`) | 200 |

### Domain verification vs Cloudflare / outages

- **Restored:** `swcmain185.live` was `failed` only because the 15:15 UTC re-check ran during
  the suspension. Every check passed again at 15:50 UTC (TXT token present; A/AAAA only
  Cloudflare addresses on the allowed list), so the single row was re-checked with the hourly
  job's own function and actor: `failed → verified`; the host serves `swc-main-185` again.
- **Fix (branch, not deployed):** lookups that cannot complete (timeout, unreachable
  nameservers, network error) now leave a domain's status unchanged and record
  `connection_error`; only definite answers (record missing, addresses elsewhere) fail it.
  `CUSTOM_DOMAIN_PROXY_RANGES` (Cloudflare's published ranges) accepts a domain whose addresses
  all lie inside them, so Cloudflare anycast changes at the tunnel cutover cannot disable it.
  Tests: `tests/test_custom_domains.py` (inconclusive keeps verified/pending; definite fails;
  error classification; proxy ranges).
- Known: the TLS probe reports `ssl_status=failed/pending` for proxied domains (it also dials
  the IPv6 addresses from a container without IPv6). Cosmetic; verification ignores it.

### New-IP migration checklist

1. ✅ Tunnel `esabong-origin` + ingress (5 hostnames); ✅ gateway path test; ✅ exposure check script.
2. ✅ Off-site backup pipeline proven on UAT and prod (local stand-in); ⏳ R2 enabled + keys → real
   upload/download/decrypt/restore test per environment → schedule (see "Encrypted off-site backups").
3. ⏳ Owner: tunnel token on the server (`put-secret.sh`), backup passphrase in the password manager.
4. ⏳ Connector readiness test (temporary connector, no DNS) → tunnel `healthy`.
5. ⏳ Owner: additional IPv4 from Contabo, configured as primary source address (VNC console;
   `deploy` has no passwordless sudo).
6. ⛔ Deploy `feature/origin-ip-hiding` to **UAT** (domain fix, `STREAM_PUBLIC_WEBRTC`), then UAT `.env`:
   `STREAM_PUBLIC_WEBRTC=false`, `CUSTOM_DOMAIN_PROXY_RANGES=<Cloudflare ranges>`, `STREAM_PUBLIC_HOSTS=<new-ip>`.
   Verify: login, WebSocket, bet → settle, player HLS ("Live · HLS"), playback `"url": ""`.
7. ⛔ Gateway in a maintenance window (1–2 s blip for both environments): checkout with the
   tunnel profile, `.env` per step 3 of "Steps", `gateway.sh up`; ports now 127.0.0.1; tunnel
   connector permanent.
8. ⛔ DNS, **UAT first**: `uat.swcmain183.live` A record → tunnel public hostname. Verify through
   Cloudflare (browser) + `origin-exposure-check.sh`. Then `swcmain183.live` + `www`, then
   `swcmain185.live` + `www` (re-verify the 185 domain right after).
9. ⛔ Prod deploy of the branch (same `.env` changes as UAT).
10. ⛔ Firewall `esabong-host-tunnel.nft` with real admin/operator addresses (rollback timer armed).
11. ⛔ Release the old IP.

### Firewall and SSH design (`infrastructure/firewall/esabong-host-tunnel.nft`)

- IPv4 and IPv6 identical; input policy drop; web ports never reachable from `eth0` (gateway on
  127.0.0.1, forward chain drops any other published port).
- SSH: day-to-day through a Cloudflare Access application (tunnel ingress `ssh://` to the host,
  allowed from the tunnel network 10.250.250.0/29; one-time PIN for named admins) → no inbound
  SSH needed; emergency `admin_v4/admin_v6` allowlist; Contabo VNC console cannot be locked out.
- `firewall.sh apply` arms an automatic rollback (default 5 min) and requires the reviewed
  SHA-256; `confirm` only after a new SSH session works.
- Streaming ports (1936, 8189, 8190) only from `operators_v4/operators_v6`.

### Rollback per change

| Change | Rollback | Time |
|---|---|---|
| DNS hostname → tunnel | Cloudflare: delete the tunnel CNAME, recreate the proxied A record (old value kept in the change log) | ~1 min (proxied, no TTL wait) |
| Gateway with tunnel/localhost ports | gateway `.env`: `COMPOSE_PROFILES=`, `HTTP_PORT=80`, `HTTPS_PORT=443`, previous `GATEWAY_TRUSTED_PROXIES`; `gateway.sh up` | seconds (exposes the IP again) |
| `STREAM_PUBLIC_WEBRTC=false` | `true`, `docker compose up -d api worker` | seconds |
| Branch deploy | `deploy.sh <previous tag>` (no migration in the branch) | minutes |
| Host firewall | `firewall.sh rollback` (or the armed timer) | immediate |
| Domain proxy ranges | unset `CUSTOM_DOMAIN_PROXY_RANGES` (gateway IP list applies again) | seconds |
