# Shared gateway reload plan (needs separate approval)

The shared gateway (`esabong-gateway-gateway-1`) terminates TLS for **both** production and UAT.
Not part of the production security release; nothing here has been run on the server.

## What is running today (read-only check, 2026-11-20)

- Compose project in `/opt/esabong/gateway/infrastructure/gateway` (its own checkout, at tag
  `uat-20261103-rc.1` plus **local edits**: `Caddyfile`, `Caddyfile.uat-only` modified).
- The running `/etc/caddy/Caddyfile` (sha256 `c81ffe08…`) equals the repository version of
  **294eaf5 (2026-11-05)**; `entrypoint.sh` and `docker-compose.yml` equal **97cf204 (2026-11-03)**.
  `GATEWAY_MODE=dual`. Container up since 2026-11-14.
- So the gateway is behind `main` by more than the security hardening: `a422b3b` (2026-11-14, tenant
  implementation) changed custom-domain routing (production first, then UAT for hosts UAT's API
  approves), added `strict_sni_host` and a loopback TLS-approval broker on :8082. Those are already
  in production's origin/API but **not** in the gateway.

## Change set

| Option | Content | Risk |
|--------|---------|------|
| **A (recommended)** | Running file (294eaf5) + only the security hardening: `read_header 15s`, `max_header_size 64KB`, `protocols h1 h2`, `trusted_proxies`/`client_ip_headers` (empty = trust nobody), `X-Forwarded-For {client_ip}` and drop `X-Real-IP`/`Forwarded`, `-Server`/`-Via`, 16 MB body ceiling, owner allowlist on `client_ip` | One concern per change; the a422b3b routing is planned separately |
| B | Repository file of the release (a422b3b routing + hardening) | Also activates custom-domain UAT fallback and the TLS broker for the first time |

The hardening is validated by `scripts/test-edge-hardening.py` (35/35, against the full repository
file; for option A the harness is rerun on the patched file before the window).

## Expected interruption

- `caddy reload` (via `gateway.sh reload`) is graceful for HTTP: requests in flight finish on the old
  config, new requests use the new one, no listener is closed — **no HTTP errors expected**.
- **Every WebSocket through the gateway (production and UAT) is closed once** when the old config is
  unloaded (Caddy closes upgraded streams on reload; `stream_close_delay` is not set). Portals
  reconnect on their own in 1–2 s (backoff 1 s → 30 s) and resync; the Declarator desk resyncs its
  monitor. LL-HLS players re-request the next part (one part, 200 ms). WebRTC media (UDP 8189/8190)
  does not pass through the gateway and is unaffected.
- Only if the container cannot see the new file (single-file bind mount keeps the old inode after
  `git checkout`): `gateway.sh up` recreates the container — **~1–3 s with no listener on 80/443 for
  both environments** plus the WebSocket reconnects. Avoid by editing the file in place (option A
  patch applied with `cp` over the same inode) and checking before reloading.
- Schedule: no live production event (no `live` event, no MediaMTX readers, no desk traffic for
  15 min), outside :04–:06 and :14–:16.

## Procedure

1. **Pre-validate without touching the live gateway** (on the server): start a throw-away Caddy on
   `127.0.0.1:18443`, joined to `esabong-prod-gateway` and `esabong-uat-gateway`, with the candidate
   file and `local_certs`; drive it with `curl --resolve` against the real origins:
   portals, `/api/ready`, WebSocket upgrade 101, an HLS playlist of a live stream (if any), forged
   `X-Forwarded-For` (API log shows the real client), no `Via`/`Alt-Svc`/`Server`, 70 KB header → 431,
   slow headers cut at 15 s. Remove the container afterwards.
2. Back up: `cp -p Caddyfile Caddyfile.bak-<date>` (and `entrypoint.sh`, `.env`) in the gateway
   directory; record `sha256sum` of each and of the running `/etc/caddy/Caddyfile`.
3. Apply the candidate **in place** (`cat candidate > Caddyfile`, keeps the inode); confirm
   `docker exec esabong-gateway-gateway-1 grep -c read_header /etc/caddy/Caddyfile` → `1`.
4. `infrastructure/scripts/gateway.sh validate` (adapts the file with the entrypoint's checks), then
   `infrastructure/scripts/gateway.sh reload`.
5. Smoke tests (below) within 2 minutes; watch `docker logs esabong-gateway-gateway-1` and both
   APIs' `ws_connected` counts return to their previous level within ~1 minute.

## Smoke tests (production and UAT)

| Check | Expected |
|-------|----------|
| `https://swcmain183.live/`, `/admin/`, `/owner/`, `/api/ready`, `/api/v1/public/tenant`; same on `uat.swcmain183.live` | 200 |
| WebSocket upgrade on `/api/ws` (monitor `check.sh` style) | 101 |
| A live HLS playlist / WHEP offer (if streaming) | 200 / 201 |
| `curl -sI` response headers | no `Server`, `Via`, `Alt-Svc`; CSP/HSTS/nosniff present |
| Request with `X-Forwarded-For: 203.0.113.77` | API request log shows the real client address |
| 70 KB header (HTTP/1.1) | 431 |
| Custom domain (if any is verified) | still routed to its environment |
| Owner console from an allowed address | 200 (owner allowlist unchanged: empty = everyone) |
| `infrastructure/monitoring/check.sh` in both checkouts | no new alerts |

## Rollback (seconds)

`cat Caddyfile.bak-<date> > Caddyfile` (in place), `gateway.sh reload` — same graceful behaviour
(WebSockets reconnect once more). If the container had to be recreated, `gateway.sh up` with the
backed-up files. Certificates live in the `gatewaydata` volume and are unaffected either way.

## Not included (separate approvals)

Cloudflare / `GATEWAY_TRUSTED_PROXIES`, DNS changes, host firewall tables, option B's custom-domain
routing change.
