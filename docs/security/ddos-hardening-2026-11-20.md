# DDoS hardening: audit, migration and protection plan (2026-11-20)

Scope: production (`swcmain183.live`, `swcmain185.live`) and UAT (`uat.swcmain183.live`) on the
shared Contabo VPS. Everything in §1 was gathered **read-only**: SSH as `deploy` (no sudo), DNS
lookups, single TCP/TLS connections to the origin, Cloudflare API `GET`s. Nothing was changed.
Runbook for the IP change: [origin-ip-change.md](origin-ip-change.md). Earlier layers (API edge
guard, gateway, WebSocket limits): [README.md](README.md), [cloudflare.md](cloudflare.md).

No configuration makes the platform DDoS-proof. The goal is that attacks hit Cloudflare (which
absorbs them) instead of the origin, and that the origin stays undiscoverable and recoverable.

## 0. What happened

The origin IPv4 was flooded directly (05:59 UTC and from ~10:20 UTC), bypassing Cloudflare.
Contabo null-routed / suspended the address to protect its network and lifts the block on a
timer; the attack resumed each time. Cloudflare only protects traffic sent **to Cloudflare**; an
attacker who knows the origin address goes around it. §1 shows how the address is still being
revealed today.

## 1. Audit findings (prioritized)

### Critical: the origin address is discoverable

| # | Finding | Evidence |
|---|---------|----------|
| C1 | **`www.swcmain183.live` and `www.swcmain185.live` resolve to the origin IP right now.** Both are DNS-only (grey) CNAMEs to the proxied apex; Cloudflare answers a grey record with the origin address. | Cloudflare API: `CNAME www → apex, proxied: false` in both zones; `dig +short A www.swcmain183.live` returns the origin IP |
| C2 | The origin answers HTTPS for every hostname to anyone: valid certificates for both domains by SNI, and the application with a `Host` header (`/api/ready` → 200). Internet-wide scanners map domain → IP this way. | direct TLS to :443; `curl --resolve` → 200 |
| C3 | **RTMPS 1936 is public and presents `CN=swcmain183.live` even without SNI**: one connection to the bare IP names the domain. | `openssl s_client` to :1936 |
| C4 | Every WebRTC answer to a player carries the origin address (`STREAM_PUBLIC_HOSTS`); UDP/TCP 8189 (prod) and 8190 (UAT) are public. | prod/UAT `.env`; MediaMTX ports `0.0.0.0` |
| C5 | Tenant admins are shown the origin IP in the custom-domain instructions (`CUSTOM_DOMAIN_GATEWAY_IPS` starts with it). | prod/UAT `.env`; `tenants/service.py` `verification_instructions` |
| C6 | Pre-cutover DNS history (A records → origin until this morning) is permanent in public DNS-history services. The current IPv4 cannot be hidden again. | `cloudflare.md` §12 |

### High

| # | Finding | Evidence |
|---|---------|----------|
| H1 | **No host firewall is active** (`nftables` inactive, `esabong-host-firewall` not installed, `ufw` inactive). Only `fail2ban` runs. Everything listening is reachable. | `systemctl is-active` |
| H2 | **Public IPv6** on `eth0` (`2400:d320:…/64`); gateway (80/443) and SSH listen on `[::]`. Not in DNS (AAAA records are Cloudflare's), but it is a second origin address to protect and to migrate. | `ip -br addr`, `ss -tln` |
| H3 | **Backups exist only on the same VPS** (56 dumps, 227 MB in `/opt/esabong/prod/db/backups`; no off-server job found for `deploy`). A provider suspension, disk loss or compromise loses data and backups together. | `ls`, `crontab -l` |
| H4 | SSH (22) open to the internet on IPv4 and IPv6; `PermitRootLogin yes` (keys only; passwords off). | `sshd_config`, port scan |
| H5 | No Cloudflare notifications configured (no DDoS / origin / tunnel alerts). | `alerting/v3/policies` → `[]` |
| H6 | Production, UAT and media share one VPS and one uplink: an attack on any of them (UAT, a stream port) takes all three down. | architecture |

### Medium

| # | Finding |
|---|---------|
| M1 | Security level is **Under Attack** on both zones. Right during the incident, but every first visit gets an interstitial. The real-time skip rule (`/api/ws`, `/stream/`) skips Browser Integrity Check, Security Level and UA blocks, but **Bot Fight Mode is on and cannot be skipped on the Free plan**: watch for challenged WebSocket/HLS requests. |
| M2 | Owner console (`/owner/`) and platform API are reachable from anywhere (`PROD_OWNER_ALLOWED_IPS` empty); protected by credentials only. |
| M3 | Production API edge guard runs in `monitor` (UAT `enforce`). |
| M4 | Only one rate-limit rule (sign-in), the Free plan maximum. API-wide limits rely on the origin's edge guard. |
| M5 | The shared gateway checkout is on `uat-20261103-rc.1`, behind `main`; gateway changes (tunnel) must be rolled out deliberately (it serves prod and UAT). |
| M6 | `always_use_https` off at Cloudflare (the origin redirects instead; one extra origin hit per HTTP request). |

### Already in place (keep)

Full (strict) SSL, TLS ≥ 1.2, WebSockets on, Cloudflare managed Free ruleset and L7 DDoS ruleset
active, scanner-probe block rule, cache bypass for `/api/`, `/stream/` and the SPA shell,
sign-in rate limit; PostgreSQL, Redis, Caddy admin and MediaMTX API not published; containers
with `cap_drop: ALL` / `no-new-privileges`; DMARC `p=quarantine`, no MX (no mail to the origin);
host-based tenant routing (`swcmain185.live` → tenant `swc-main-185`, apex and
`*.swcmain183.live` → `swc-main-183`, the stream provider).

## 2. New IP migration plan

Decided: an **additional Contabo IPv4** on the same VPS (no data move), **Cloudflare Tunnel**,
**HLS for public playback now**, separate media server later. Full steps in
[origin-ip-change.md](origin-ip-change.md); additions from this audit:

1. **Before anything else:** an off-server copy of the latest verified prod and UAT dumps (H3),
   plus the Docker volumes that hold state not in Postgres (`*_caddydata`, `gatewaydata`,
   `*_recordings` if needed).
2. **IPv6:** do not publish AAAA for the origin. Either remove the public IPv6 from `eth0` or
   firewall it like IPv4 (the tunnel does not need inbound IPv6).
3. **`www` records:** recreate them as tunnel hostnames (or proxied), never DNS-only (C1).
4. **Gateway checkout:** move `/opt/esabong/gateway` to the release that contains the tunnel
   profile in a maintenance window (`gateway.sh up` recreates the gateway: ~1–2 s for both
   environments).
5. Tenant isolation: each tunnel public hostname keeps its own `Host`
   (`swcmain183.live`, `www.swcmain183.live`, `uat.swcmain183.live`, `swcmain185.live`,
   `www.swcmain185.live`); the gateway and app route by `Host` exactly as today.

## 3. Cloudflare configuration (both production zones; UAT inherits swcmain183.live)

| Setting | Target | Now |
|---------|--------|-----|
| DNS | Every record that reaches the origin is a tunnel CNAME (orange); no A/AAAA holds an origin address | www grey (**C1**) |
| SSL/TLS | Full (strict); Always Use HTTPS on; min TLS 1.2 | strict / off / 1.2 |
| Origin auth | Cloudflare Tunnel (replaces Authenticated Origin Pulls) | none |
| WAF | Managed Free ruleset on; scanner block rule; real-time skip rule | in place |
| HTTP DDoS | Managed L7 ruleset (default, always on) | in place |
| Rate limiting | Sign-in rule (Free: 1 rule); more rules need Pro | in place |
| Bots | Bot Fight Mode on; verify WS/HLS not challenged (M1) | on |
| Security level | Under Attack during incidents; back to Medium/High afterwards, keep the skip rule | Under Attack |
| Access | Cloudflare Access (free ≤ 50 users) on `/owner/*` and `/api/v1/platform/*` of `swcmain183.live`; one-time PIN or Google for the owner team (M2) | none |
| Alerts | HTTP DDoS attack alert, Tunnel health, Passive origin monitoring, to the ops mailbox (H5) | none |

WebSockets (`/api/ws`) and API calls keep working through the tunnel; the skip rule must stay
first in the custom rules.

## 4. Origin protection: Tunnel vs proxy + firewall

| | Cloudflare Tunnel (chosen) | Proxy + origin firewall (`origin-lock.sh`) |
|---|---|---|
| Inbound web ports | none (gateway bound to 127.0.0.1) | 80/443 open to Cloudflare ranges only |
| Scanner discovery | nothing answers on the IP | connections from non-Cloudflare sources dropped; IP still answers Cloudflare |
| Breaks if | tunnel down (two cloudflared connections, auto-reconnect) | Cloudflare ranges change (refresh job) |
| Certificates | origin cert irrelevant (No TLS Verify inside Docker) | LE renewals via Cloudflare must keep working |

Origin controls with either choice:
- **Docker bypasses the host INPUT chain** for published ports. Keep services unpublished or
  bound to `127.0.0.1`. Filtering a published port needs `DOCKER-USER`/forward rules, not `ufw`.
- **SSH:** allow only admin addresses (IPv4 and IPv6), or reach it through Cloudflare Access SSH
  / a VPN; `PermitRootLogin prohibit-password` → `no` once a sudo user is confirmed.
- **Emergency access that cannot be locked out:** the Contabo VNC console works with the network
  cut or the firewall wrong. Keep a root/sudo password for it in the password manager.
  Every firewall change is applied with an automatic rollback timer (e.g. `nft` + `at now + 5 min`
  restore) until verified.
- PostgreSQL/Redis stay unpublished (verified).

## 5. Streaming

| Protocol | Through Cloudflare? | Plan |
|----------|-------------------|------|
| LL-HLS (`/stream/hls/…`) | **Yes** (HTTPS, tunnel) | Public playback now (`STREAM_PUBLIC_WEBRTC=false`); 2–6 s more latency than WebRTC. Video volume on the Free plan: check Cloudflare's CDN terms before large audiences |
| WebRTC playback (UDP/TCP 8189/8190) | **No** (UDP media; not carried by the proxy or tunnel) | Broadcaster staff only, until media moves to a **dedicated media VPS** whose exposure cannot take down the app |
| WHIP browser publishing | Signalling yes, media no | Operators only; later on the media VPS |
| RTMP / RTMPS ingest (1935/1936) | **No** (Spectrum, paid) | Close 1936 publicly (C3); OBS ingest only from allowlisted operator IPs or via the media VPS |

Low latency for players again: WebRTC from the media VPS (sacrificial address, its own DDoS
exposure) or a managed provider (e.g. Cloudflare Realtime/Stream). Both are later decisions with cost.

## 6. Application security (review; details in README.md §3)

- **Tenant isolation:** host-based tenancy verified for all three hostnames; signed playback
  claims carry tenant + user; MediaMTX authorizes per session (tested: cross-tenant claims
  rejected).
- **Rate limits:** API edge guard (prod `monitor` → `enforce` after reviewing logs, M3), login
  lockout, Cloudflare sign-in rule.
- **WebSockets:** token auth, per-address limits (README §3.3).
- **CORS:** explicit origins, `*` rejected at startup.
- **Secrets:** `.env` files mode 600; the tunnel token is a new secret (gateway `.env`, never in git).
- **Containers:** `cap_drop: ALL`, `no-new-privileges`, PID limits.
- **Database access:** app/platform roles with RLS; no published DB ports.

## 7. Monitoring and recovery

- External uptime checks (any free monitor) on `/api/ready` of all three hostnames, 1-minute
  interval, alert to phone/email.
- Cloudflare notifications (§3).
- Off-server backups: nightly encrypted dump copied off the VPS (e.g. Cloudflare R2 or
  Backblaze B2; **not** Contabo Object Storage Singapore, which has an open incident), with a
  monthly restore test (`db/scripts/verify-backup.sh`).
- Incident runbook: Contabo suspension email → wait/ticket; VNC console for access; rollback per
  [origin-ip-change.md](origin-ip-change.md).

## 8. Verification

**Today (read-only):** sites answer through Cloudflare (403 = Under Attack challenge for
non-browsers); origin direct `/api/ready` 200; containers healthy; DB at 0041; DNS/cert/port
findings as in §1. Login, WebSockets, video and betting were **not** exercised end-to-end today
(origin intermittently suspended; challenge blocks scripted clients).

**After migration (UAT first, then prod):** the checks in origin-ip-change.md §5, plus:
- direct TCP to the new IPv4/IPv6 on 80/443/1936/8189/8190 refused;
- `dig` for every hostname (incl. `www`) returns only Cloudflare addresses;
- login, WebSocket updates, bet → settle, HLS playback, declarator desk, tenant isolation
  (`swcmain185.live` shows tenant `swc-main-185` only).

No load or attack testing.

## 9. Remaining risks and dependencies

- The current IPv4 stays burned; anything that still points at it will be attacked.
- **Contabo** decides suspensions; it has no always-on volumetric protection comparable to a
  DDoS-protected host. If the new IP leaks, the same outage repeats.
- **Cloudflare** (Free plan): one rate-limit rule, Bot Fight Mode not skippable, video terms;
  the tunnel and the whole site depend on Cloudflare's availability.
- WebRTC/RTMP cannot be hidden behind Cloudflare; low latency needs the media VPS or a provider.
- Human leaks: the new IP must never be pasted into chats, tickets (other than Contabo's),
  commits or screenshots.
