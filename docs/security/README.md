# Security hardening: edge guard, DDoS layers and build protection

Status (2026-11-20): implemented and validated locally; **not deployed**. UAT first, production only
with explicit approval (see [Deployment](#deployment-and-rollback)). Nothing here changes betting,
odds, payouts, settlement, wallets, commissions, roles, UI or API contracts: every control sits
around the application, is switched by configuration, and can be removed on its own.

- [1. Audit findings](#1-audit-findings-phase-1)
- [2. Architecture](#2-architecture)
- [3. What was implemented](#3-what-was-implemented)
- [4. Environment differences](#4-environment-differences)
- [5. Upstream DDoS / WAF plan (Layer 1)](#5-upstream-ddos--waf-plan-layer-1)
- [6. Rate-limit policy](#6-rate-limit-policy)
- [7. Test results](#7-test-results)
- [8. Performance](#8-performance)
- [9. Deployment and rollback](#deployment-and-rollback)
- [10. Operations runbook](#10-operations-runbook)
- [11. Remaining risks and limitations](#11-remaining-risks-and-limitations)

---

## 1. Audit findings (Phase 1)

Read-only inspection of the repository and the Contabo VPS (no change, no restart), 2026-11-20.

| # | Finding | Severity | Action |
|---|---------|----------|--------|
| A1 | **Zombie processes**: 491 on the host, all children (`celery`, `grep`) of the Celery worker master. UAT worker at **484 of 512 PIDs** after 67 h (~7/h); production worker 9 zombies (1 CPU, fewer timeouts). At the limit the worker cannot fork (pool, healthchecks). | **High** (UAT, within hours) | Root cause found and reproduced; fixed with `init: true` (§3.6). Existing zombies disappear only when the worker container is recreated (next deploy). |
| A2 | No request-rate limiting anywhere except sign-in failure lockouts, registration and signup throttles. | High | Edge guard (§3.2). |
| A3 | Client address trust: the gateway replaces client `X-Forwarded-For` (Caddy default for untrusted peers), uvicorn takes the left-most entry (`--forwarded-allow-ips '*'`). Safe today, but enabling Cloudflare without gateway changes would either make every visitor appear as a Cloudflare address (per-address limits and the sign-in IP lockout would hit everyone) or make the address forgeable. | High (latent) | Gateway writes exactly one verified address; Cloudflare mode prepared and tested (§3.4, §5). |
| A4 | No slow-header (slowloris) timeout; Go's 1 MB header limit; no request-body ceilings at the edge. | Medium | Gateway `read_header 15s`, 64 KB headers, body ceilings per route (§3.4). |
| A5 | Portal hosts answer `/.env`, `/.git/config`, `*.map`, `/wp-admin/install.php` with **200** (the SPA shell, not the file). Not a leak, but scanners read it as "keep probing": 25 scanner sources in 72 h, one with 33 probes. | Low | 404 for those paths (§3.5). |
| A6 | `Via: 1.1 Caddy` and `Alt-Svc: h3` advertised; UDP 443 is not published, so browsers try QUIC first for nothing. | Low | Removed (§3.4). |
| A7 | Production portal builds: no source maps, no `sourceMappingURL`, Angular dev mode off, no secrets in bundles (verified on the live site and a local build). Nothing enforced it, though. | Info / Low | Explicit `sourceMap: false`, build gate in the image (§3.1). |
| A8 | API docs/OpenAPI off in production and UAT (404); `/api/v1/internal/*` unreachable from outside; stack traces never returned (generic 500 envelope); access logs strip `?token=`, `Authorization`, `Cookie`. | Info (good) | Kept. |
| A9 | WebSockets: token + tenant match + revocation + active-account checks; channels are chosen by the server, so cross-tenant subscription is impossible. No handshake/concurrency/message-rate limits; uvicorn accepts 16 MiB frames. | Medium | WebSocket limits and 1 MiB frames (§3.3). |
| A10 | Streaming: publish needs a per-stream key, viewing a playback token (API auth callback); MediaMTX API/metrics need credentials; HLS passes through Caddy only for exact paths. Production RTMPS **1936** and WebRTC **8189** are publicly reachable (intended for streaming), plain RTMP is loopback-only. | Info | Recommendations in §5.3. |
| A11 | Host: PostgreSQL/Redis publish no host port; no Docker TCP socket; env files mode 600; unattended-upgrades and Fail2Ban active; no reboot pending; 10 non-security updates. sshd: `PasswordAuthentication no`, but **`PermitRootLogin yes`** (keys) and `X11Forwarding yes`. | Medium | Recommendation (needs owner approval, §11). |
| A12 | The repository host firewall (`esabong-host.nft`) allows only 80/443/8190, yet 1936 and 8189 are reachable: that table is **not loaded** in production as committed (loading it unchanged would cut production streaming). | Medium | Note for whoever applies it: add the production media ports first (§11). |
| A13 | Containers: PID and memory limits, log rotation (20 MB × 5), CPU weights and OOM priorities already set per environment; all app processes run as uid 999. No `no-new-privileges`, default capability set everywhere. | Low | `no-new-privileges` everywhere, `cap_drop: ALL` for app-user services (§3.6). |

### Traffic baseline (gateway logs, 72 h, both environments)

| Class | Busiest address, per minute | Per second |
|-------|---------------------------|-----------|
| HLS (one viewer) | **861** | 25 |
| API reads | 76 | 13 |
| API writes | 33 | 3 |
| Declarator desk reads / writes | 57 / 22 | 14 / 5 |
| Sign-in / refresh | 13 | 4 |
| WebSocket handshakes | 6 | 3 |
| All API classes, one address | 109 | – |

285 client addresses, p99 924 requests/min for the whole site. HLS is the bulk of requests, which is
why no request limit may ever apply to `/stream/*` (and none does: HLS goes from Caddy straight to
MediaMTX, never through the API).

### Zombie root cause (A1)

The worker healthcheck is `CMD-SHELL celery … inspect ping … | grep -q pong` with a 20 s timeout. On
a throttled CPU (UAT worker: 0.5 CPU) `celery inspect` can take longer; Docker then kills the `sh` it
started, its two children are re-parented to PID 1 of the container — the Celery master, which does
not reap processes it did not start — and stay as zombies. Reproduced locally (same image, PID 1 a
plain Python process, a healthcheck pipeline that times out): **18 zombies in 30 s** without an init
process, in `sleep`+`grep` pairs exactly like production's `celery`+`grep`; **0** with `--init`.
Note: `docker top` cannot show them (cgroup v2 drops zombies from `cgroup.procs`), but they still
count toward `pids.max`.

---

## 2. Architecture

```
 Internet
    │   (Layer 1, prepared, not enabled: Cloudflare proxy + WAF in front of 80/443; see §5)
    ▼
 Host nftables ── esabong_edge_guard (opt-in): per-address open/new connection caps on 80/443
    │             esabong_host (existing, not loaded in prod): port allowlist, SSH sources
    ▼
 Gateway Caddy (shared, TLS) ── Layer 2: header timeout 15 s, 64 KB headers, 16 MB bodies,
    │                           one verified client address, no banners, owner allowlist on client_ip
    ▼
 Origin Caddy (per environment) ── per-route body ceilings, portal file hygiene, security headers
    │                    │
    │                    └── /stream/* ──► MediaMTX (HLS / WHEP / WHIP; token-gated; never rate-limited)
    ▼
 FastAPI ── Layer 3/4: EdgeGuardMiddleware (Redis counters, temporary blocks, WS limits), then routers
    │
    ├── PostgreSQL, Redis (internal network only)
    └── Celery worker (init: tini reaps orphans), beat
```

Trust boundary for the client address: only the gateway decides it (`{client_ip}`), everything
behind it trusts private-range peers only.

---

## 3. What was implemented

### 3.1 Browser / build protection

- `web/angular.json`: production configurations set `sourceMap: false` and `namedChunks: false`
  explicitly (they were defaults); development keeps `sourceMap: true`, no optimisation.
- `web/scripts/verify-prod-bundle.mjs`: fails the build on source-map files or references, the
  Angular development-mode banner, private keys, secret **values** (`JWT_SECRET=…`), credentials in
  URLs, cloud keys, local developer paths, hidden files and Finder copies. Run by
  `infrastructure/docker/web.Dockerfile` after `ng build`, so no UAT/production image can ship them.
  A bare variable name in help text (the admin "set MEDIAMTX_API_PASSWORD" hint) is allowed.
- Caddy (`(spa)` snippet): `*.map`, hidden files (except `/.well-known/`), Finder `name 2.ext` copies
  and scanner probe paths (`*.php`, `/wp-admin/*`, …) answer 404 instead of the portal shell.
- Security headers: existing CSP/HSTS/nosniff/COOP/Referrer/Permissions-Policy kept; added
  `X-Frame-Options: DENY`, `X-Permitted-Cross-Domain-Policies: none`; no `Server`/`Via`.
- Not done on purpose: keyboard-shortcut blocking, `debugger` loops, disabling right-click or
  DevTools. They give no security (everything shipped to a browser is readable), break
  accessibility and browser features, and the backend already authorises every operation itself.
- Console output: the only `console.*` call in portal code is the bootstrap error handler; nothing
  logs tokens, balances or API internals. Unchanged.

### 3.2 API edge guard (`api/app/security/`)

Pure ASGI middleware placed inside the request-id and security-header middleware, so a refusal is the
normal API error envelope with `X-Request-ID`:

```json
{"error": {"code": "rate_limited", "message": "Too many requests. Try again shortly.", "details": {}, "request_id": "…"}}
```
with `429`, `Retry-After` and `Cache-Control: no-store` (413 `request_too_large` for oversized bodies).

| Rule | Key | Default / min | Applies to |
|------|-----|---------------|-----------|
| Flood ceiling | client address | 3000 | every API request except exempt |
| Signed-in budget | account (signature-checked JWT) | 1200 | non-critical routes |
| Anonymous budget | client address | 600 | requests without a valid token |
| Auth budget | client address | 120 | login, refresh, register, signup, licensing |
| Body size | Content-Length | 1 MiB (uploads 8 MiB) | every API request |
| Temporary block | client address | 3 strikes / 10 min → 10 min | addresses breaking the flood ceiling |
| Concurrency caps | route prefix, per worker | none | optional (`SECURITY_CONCURRENCY_LIMITS`) |

Guarantees (each covered by a test, §7):

- **Critical routes never refuse signed-in callers**: `/api/v1/declarator/`, `/portal/matches/`
  (bets), `/portal/betting`, `/portal/bets`, `/admin/matches/`, `/admin/events/`, `/admin/betting`,
  `/admin/balance`, `/admin/points`, `/resources/`, `/loader/`, `/commissions/`, `/simulations/`,
  `/agent/`. They are counted and logged (`observed_critical`), not refused — even for a blocked
  address. Anonymous floods against them are still limited.
- **Exempt**: `/api/health`, `/api/ready`, `/api/v1/internal/*` (MediaMTX's auth callback for every
  viewer, Caddy's TLS check), loopback and `SECURITY_ALLOWLIST`.
- **Per account, not per NAT**: two players behind one carrier-grade NAT address keep separate budgets.
- **Fail open**: Redis error or no answer within 100 ms → the request is served unguarded and one
  `security_store_unavailable` line a minute is logged.
- **Monitor mode** counts and logs `would_refuse` / `would_block` and never refuses or blocks.
- **No permanent bans**: blocks expire (max 24 h for manual ones) and can be lifted at once (§10).
- Idempotency, transactions and handlers are untouched: the guard only decides before the request
  reaches a router; it never cancels a running request.

### 3.3 WebSocket protection

Same middleware, `/api/ws` and `/api/ws/simulations`:

| Rule | Default | Effect when enforced |
|------|---------|---------------------|
| Handshakes per address | 300/min | upgrade refused (client backs off 1 s → 30 s) |
| Handshakes per account | 60/min | same |
| Open sockets per address | 1000 | same |
| Open sockets per account | 30 | same |
| Client frames | 60 per 10 s (portals send 1 ping / 25 s) | socket closed with 1008 |
| Frame size (uvicorn) | 1 MiB (was 16 MiB) | uvicorn closes with 1009 |

Open sockets are tracked in Redis sorted sets with 150 s expiry, refreshed every 60 s and released on
close, so a crashed worker cannot leak slots. Existing authentication, tenant isolation, heartbeat,
token-expiry close (4408) and reconnect behaviour are unchanged; the server never adds latency to
frames (only the client→server direction is counted).

### 3.4 Gateway (shared Caddy) — Layer 2

`infrastructure/gateway/Caddyfile` and `Caddyfile.uat-only`:

- `timeouts { read_header 15s }` (also bounds the TLS handshake); no body/idle/write timeouts, so
  uploads, long LL-HLS requests and WebSockets are unaffected.
- `max_header_size 64KB` → 431 (Go default 1 MB).
- `request_body { max_size 16MB }` outer ceiling.
- `protocols h1 h2`: stop advertising HTTP/3 (UDP 443 is not published).
- `header_up X-Forwarded-For {client_ip}`, drop `X-Real-IP` and `Forwarded`: the origin and API see
  exactly the address the gateway verified. The owner allowlist matches `client_ip`.
- `trusted_proxies static {$GATEWAY_TRUSTED_PROXIES}` + `client_ip_headers {$GATEWAY_CLIENT_IP_HEADER}`:
  empty by default (= today: trust nobody). `entrypoint.sh` refuses `0.0.0.0/0`, `::/0`, private and
  loopback ranges and unknown headers.
- `-Server`, `-Via` on every response.

Origin (`infrastructure/caddy/snippets.caddy`, `(body-limits)`): `/api/*` 10 MB, `/stream/*` 256 KB
(SDP offers are a few KB). Caddy's standard rate-limit module is not part of the official image, so
request-rate limiting lives in the API (Redis, shared by all workers) and connection limiting in
nftables — both tested — rather than a custom Caddy build.

### 3.5 Host — per-address connection caps (`infrastructure/firewall/`)

`esabong-edge-guard.nft` + `edge-guard.sh`: a separate table `inet esabong_edge_guard` (never the host
table, Docker's or Fail2Ban's) on the forward path to the published 80/443:

- more than **2000 open connections** from one IPv4 address, or
- more than **100 new connections/s** (burst 600) from one address

→ counted and remembered for 15 minutes (`observe`, the default after `apply`), or reset (`enforce`).
Exempt set for admin/monitoring addresses and, later, the upstream proxy's ranges. Not loaded at boot
(a reboot is a rollback). Every rule change needs owner approval.

### 3.6 Containers

- `worker`: `init: true` (tini as PID 1 reaps orphans) — the zombie fix (A1).
- Contabo overlay: `no-new-privileges:true` for every service; `cap_drop: [ALL]` for api, worker, beat,
  migrate and mediamtx (all uid 999, ports > 1024). Postgres, Redis and Caddy keep Docker's default
  capability set (they start as root to switch user or bind :80). Verified: each image starts and
  serves under these settings.
- uvicorn `--ws-max-size 1048576` (Dockerfile and Contabo command, `API_WS_MAX_BYTES`).

### 3.7 Logging and monitoring

- Security events: JSON lines on the `security` logger (`docker compose logs api | grep '"security"'`)
  with `event`, `rule`, `action` (`refused`, `would_refuse`, `observed_critical`, `blocked`,
  `would_block`, `unblocked`), `client_ip`, `method`, `path` (never the query string), `security_class`,
  `status`, counts/limits, `env` (`prod`/`uat`/`local`, from `ESB_STACK`), `mode`, `request_id`.
  Sampled (1st, 10th, 100th, every 1000th over a limit) so a flood cannot flood the log. Never
  tokens, passwords, cookies, bodies or payment data.
- Every API request line now also carries `client_ip` and `security_class`.
- Temporary blocks and manual block/unblock are written to the platform audit log
  (`security.ip_blocked`, `security.ip_unblocked`, actor `edge-guard` / `edge-guard-cli`).
- `infrastructure/monitoring/check.sh` (existing 5-minute monitor) now also alerts on: 429 share at
  the edge above 2 %, any new temporary block, more than 50 logged refusals, containers above 80 % of
  their PID limit, more than 20 zombies in a container (it reports UAT's worker today).
- `infrastructure/monitoring/traffic-report.py`: read-only classification + anomaly report over Caddy
  logs (HLS, WebSocket, polling, desk, health, static first; flags only credential guessing, 404
  scanning, probe paths, reconnect loops, API floods and 429s). `--anonymize`, `--json`.

---

## 4. Environment differences

| | Local | UAT | Production |
|---|---|---|---|
| Angular build | `ng serve` / development: source maps, dev mode, console | production build, gate enforced | production build, gate enforced |
| `SECURITY_GUARD_MODE` | `off` (default when `ENVIRONMENT≠production`) | **`enforce`** | **`monitor`** first, `enforce` after review |
| `SECURITY_ENV_NAME` (logs) | `local` | `uat` | `prod` |
| Gateway hardening | n/a (single-host `Caddyfile` gets body limits only) | yes (shared gateway) | yes (shared gateway) |
| API docs | on | off | off |
| Upstream proxy trust | none | none (until Cloudflare) | none (until Cloudflare) |
| nftables edge guard | n/a | shared host: observe → enforce with approval | same table (one host) |
| Separate counters | own Redis | own Redis (`esabong-uat`) | own Redis (`esabong-prod`) |

No setting or secret is shared: each environment reads its own `.env`, Redis and logs; the gateway
only holds the trust settings (no application secret).

---

## 5. Upstream DDoS / WAF plan (Layer 1)

Origin-side controls only stop single-source abuse; a volumetric attack saturates the VPS uplink
before any of them runs. That needs an upstream network. **Not enabled** — DNS, IP addresses and
streaming endpoints are unchanged until an approved plan.

### 5.1 Recommended: Cloudflare (proxied DNS) for website + API

1. Plan: Pro or Business (WAF managed rules, rate-limiting rules, bot management on Business+).
   **Check the content terms first**: Cloudflare's self-serve plans restrict serving video through
   the CDN — keep `/stream/hls/*` off Cloudflare or use their Stream product; confirm the gaming
   content policy for your account.
2. Before switching DNS, on the gateway `.env`:
   `GATEWAY_TRUSTED_PROXIES=<all ranges from https://www.cloudflare.com/ips/>`,
   `GATEWAY_CLIENT_IP_HEADER=CF-Connecting-IP`, then `gateway.sh up`. Verified by
   `scripts/test-edge-hardening.py`: from a trusted range the visitor address comes from
   `CF-Connecting-IP`; a direct-to-origin client cannot claim an address.
3. Add the same ranges to `exempt_v4` of the nftables edge guard (all traffic then comes from them).
4. Origin lock (after a stable week): allow 80/443 only from Cloudflare ranges in the host firewall
   (`public_tcp_v4` split by source), keeping the admin SSH rule. Rotate the origin IP if it was
   public before (old address stays known).
5. Separate Production and UAT zones/hostnames rules; SSL mode **Full (strict)** (the gateway already
   has valid certificates); keep on-demand TLS for custom domains (customers point CNAMEs at a
   proxied hostname, or stay DNS-only).

### 5.2 Suggested WAF / rate rules (start in "log"/"simulate")

| Rule | Expression (summary) | Action |
|------|----------------------|--------|
| Managed rules | Cloudflare Managed + OWASP core (paranoia 1) | log 1 week → block |
| Scanner paths | path matches `\.php$`, `/wp-`, `/.env`, `/.git` | block |
| Sign-in flood | `/api/v1/auth/login` or `/platform/auth/login` > 30/min per IP | managed challenge |
| API flood | `/api/*` (not `/api/ws`) > 1200/min per IP | block 1 min |
| Owner console | `/owner*`, `/api/v1/platform/*` outside the office/admin list | managed challenge |
| Never challenge | `/api/ws*`, `/stream/*`, `/api/health`, `/api/ready` | skip (challenges break sockets/players) |
| Countries/ISPs | none | not without explicit approval |

### 5.3 Streaming (Cloudflare's HTTP proxy does not carry RTMP/RTMPS or WebRTC UDP)

- Ingest: RTMPS 1936 accepts only valid per-stream keys; restrict it to the broadcaster addresses
  with the existing `infrastructure/scripts/rtmp-allowlist.sh` (`STREAM_RTMP_PORT=1936`), or ingest
  via WHIP over HTTPS (already supported) and close 1936.
- Playback: LL-HLS through the gateway (current fallback) or WebRTC media on 8189. To hide the origin
  for WebRTC, relay through a managed TURN service (`STREAM_TURN_URLS`/`STREAM_TURN_SECRET`, already
  supported) or a dedicated streaming edge/CDN; Cloudflare Spectrum covers TCP/UDP on higher plans
  (confirm RTMP/UDP coverage and price for the account).
- Never apply request limits to HLS (one viewer = ~25 requests/s at peak).

---

## 6. Rate-limit policy

Defaults are ≥15× the measured peak of a single address (baseline above) because Philippine mobile
carriers put many players behind one address. Rules of thumb before changing anything:

1. Run production in `monitor` through at least one busy event day.
2. Read `would_refuse` lines and `traffic-report.py`. A rule may only be tightened when nothing in
   its `would_refuse` lines is a real user pattern (portals, desk, agents, monitoring).
3. Never add a limit to `/stream/*`, health checks or `/api/v1/internal/*`; never move a critical
   route out of the protected list.
4. Change one value at a time (`SECURITY_*` in the environment's `.env`, then `docker compose up -d
   api`), watch the 429 share in `check.sh` for an hour.

---

## 7. Test results

All on 2026-11-20, local machine (Docker 29.8), against base commit `52ab9f2`.

### Regression (existing behaviour)

| Suite | Before | After |
|-------|--------|-------|
| Backend `pytest` (real PostgreSQL + Redis): auth, RBAC, betting, ghost bets, odds/payout vectors, declarations, settlement/refunds, commissions, balances/transfers, isolation, WebSockets, streaming | 434 passed | **458 passed** (434 + 24 new), 0 failed |
| Frontend unit tests (`ng test`, owner / admin / player) | – | **107 / 179 / 120 passed** |
| Production builds of the three portals | – | build OK; bundle gate passed (135 files) |
| Origin config A/B, HEAD vs new, real production builds: status, content type, caching, redirects, body hash over 27 paths × 2 hosts | – | **54 pairs, 0 differences** |
| Existing gateway harness `scripts/test-custom-domain-gateway.py` | pass | pass |
| `check-env-isolation.py` templates and merged compose (prod, uat) | as before | identical to HEAD |

The UI, API responses and business rules are byte-for-byte unchanged: no portal source file, router,
service or migration was edited; the guard is `off` in local and test runs by default.

### Security tests

| Test | Result |
|------|--------|
| `api/tests/test_edge_guard.py` (24): mode defaults; classification; off = no counting; monitor logs `would_refuse` and serves; enforce returns the 429 envelope + `Retry-After`; real middleware order (`X-Request-ID`, `nosniff`, `X-Frame-Options` on refusals); per-account budgets behind one NAT address; **critical routes never refuse signed-in users, even from a blocked address**; anonymous floods on critical routes still limited; forged tokens count as anonymous; health/internal/allowlist exempt; auth budget; strikes → temporary block → CLI unblock; monitor never blocks; 413 bodies (monitor = handler's own answer); Redis down → fail open (one log line); slow Redis → no delay (50 ms budget); concurrency caps; WebSocket normal use untouched and slot released; message flood → 1008; handshake and per-account socket limits; WS off = passthrough; CLI block/list/unblock + audit rows | **24 passed** |
| `scripts/test-edge-hardening.py`: real gateway + entrypoint → real origin → echo API | **35/35 passed** |
| — forged `X-Forwarded-For` / `X-Real-IP` / `Forwarded` never reach the API; API sees `client, gateway`; uvicorn resolves the real client (default and Cloudflare mode); direct clients cannot claim an address with `CF-Connecting-IP`; owner allowlist not bypassable | pass |
| — slowloris (headers trickled every 2 s): connection cut after **15.0 s**; 70 KB header → 431; API body 11 MB → 413, stream signalling 300 KB → 413, normal bodies intact | pass |
| — no `Server`/`Via`/`Alt-Svc`; CSP, nosniff, `X-Frame-Options`; `.map`, hidden files, Finder copies, `*.php`/`/wp-admin` → 404, `.well-known` and deep SPA routes still served | pass |
| — HLS playlist and WHEP offer reach MediaMTX untouched; WebSocket upgrade still reaches the API | pass |
| — entrypoint refuses `0.0.0.0/0`, private ranges, unknown client header; accepts Cloudflare ranges | pass |
| Bundle gate negative controls (source map file, `sourceMappingURL`, dev-mode banner, `JWT_SECRET` value, `redis://:pw@`, `/Users/...` path, Finder copy) | **7/7 caught**; development build keeps **85** source maps |
| Zombie reproduction (same worker image, timing-out healthcheck) | 18 zombies/30 s without init → **0** with init |
| Images under `no-new-privileges` (+ `cap_drop: ALL` for app-user services): Postgres, Redis, Caddy (:80), MediaMTX (all listeners), API (`/api/health` 200), worker (PID 1 = docker-init, CapEff 0, uid 999) | all start and serve |
| nftables edge guard in Ubuntu 24.04 (nft 1.0.9): check/apply/status/enforce (idempotent)/observe/exempt/re-apply/rollback; functional test with tiny limits: observe lets 12/12 through and counts 7 over the cap, enforce resets, rollback restores | pass |
| New monitor checks, read-only against live UAT | detects the worker at **488/512 PIDs** and **482 zombies** |
| `traffic-report.py` on 72 h of live gateway logs | 0 real users flagged for volume (HLS 861/min and desks pass); **25 scanner sources** flagged (WordPress/PHP probes, `/.env`) |

Not run (need approval or a deployed UAT): controlled load test on UAT, WAF rule validation (no
Cloudflare zone yet), Playwright flows against UAT with `enforce` on, firewall changes on the VPS.

---

## 8. Performance

`scripts/benchmarks/edge_guard_overhead.py` (guard alone, trivial endpoint, local Redis, 4000
requests/row) and the same comparison on real routes through the full application:

| Measurement | Guard off | Guard enforce (monitor is the same) |
|-------------|-----------|-------------------------------------|
| Guard alone, sequential, anonymous / signed-in (p50) | 0.061 / 0.068 ms | 0.213 / 0.259 ms (**+0.15 / +0.20 ms**) |
| Guard alone, max throughput of one process | ~15,500 req/s | ~7,000 (anonymous) / ~5,500 (signed-in) req/s |
| `GET /api/v1/auth/me` (DB), sequential p50 / p95 | 3.94 / 4.73 ms | 3.97 / 4.70 ms |
| `GET /api/v1/admin/balance/summary`, sequential p50 / p95 | 5.73 / 6.76 ms | 6.14 / 7.43 ms |
| `auth/me`, 10 concurrent, CPU-bound, throughput | 592 req/s | 538 req/s (−9 %, cheapest route) |
| `balance/summary`, 10 concurrent, throughput | 338 req/s | 339 req/s |

Cost per guarded request ≈ 0.15–0.2 ms of API CPU (one Redis Lua round trip + JWT verification);
by the measured 5–6× vCPU factor that is roughly 1 ms on UAT and less on production. Gateway/origin
changes add no work per request (header deletion, a matcher); HLS and WebRTC media paths are
unchanged. `SECURITY_GUARD_MODE=off` removes the cost entirely.

---

<a id="deployment-and-rollback"></a>
## 9. Deployment and rollback

All steps use the existing release flow (tag → `deploy.sh <tag>`), UAT first. No migration.

### 9.1 UAT

1. Merge, tag `uat-YYYYMMDD-rc.N`, push the tag.
2. `/opt/esabong/uat/.env`: add the edge-guard block from `.env.contabo.uat.example`
   (`SECURITY_GUARD_MODE=enforce`; put the Playwright/benchmark runner address in
   `SECURITY_ALLOWLIST` during approved load tests).
3. `infrastructure/scripts/deploy.sh <tag>` (nohup, as usual). This rebuilds the images (bundle gate
   runs), recreates the containers — **the worker comes back with `init: true` and its 480 zombies are
   gone** — and loads the new origin Caddyfile.
4. Gateway (shared with production — a reload never drops connections):
   `infrastructure/scripts/gateway.sh reload`, then confirm the running container sees the new file:
   `docker exec esabong-gateway-gateway-1 grep -c read_header /etc/caddy/Caddyfile` → `1`. A single-file
   bind mount keeps the old inode after `git pull`; if it prints `0`, run `gateway.sh up` instead
   (recreates the gateway: ~1–2 s interruption for **both** environments — schedule it).
5. Verify: `scripts`-style smoke (`/`, `/admin/`, `/owner/`, `/api/ready`, `/api/v1/public/tenant` →
   200), `curl -I` shows no `Via`/`alt-svc`, `/.env` → 404, `python3 infrastructure/monitoring/traffic-report.py`
   over the last hour flags nothing new, `docker top`/`check.sh` shows 0 zombies, a UAT tester can sign
   in, bet, declare, settle, watch the stream, and the desk works under the soak harness.
6. Optional, with approval: `sudo infrastructure/firewall/edge-guard.sh apply` (observe), read
   `status` after a test event.

### 9.2 Production (explicit approval required)

Same tag after UAT sign-off; `.env` from `.env.contabo.prod.example` (`SECURITY_GUARD_MODE=monitor`).
`APPROVED_MIGRATIONS` is not needed (no migration). Switch to `enforce` later by editing `.env` and
`docker compose up -d api` (seconds; WebSockets reconnect by themselves).

### 9.3 Rollback (each layer on its own, fastest first)

| Layer | Rollback | Time |
|-------|----------|------|
| API guard | `SECURITY_GUARD_MODE=off` in `.env`, `docker compose up -d api` | seconds |
| Wrong block | `docker compose exec api python -m app.cli security unblock <ip>` | immediate |
| nftables guard | `sudo infrastructure/firewall/edge-guard.sh observe` (stop dropping) or `rollback` (remove) | immediate |
| Gateway | `git checkout <previous tag> -- infrastructure/gateway/` then `gateway.sh reload` | seconds |
| Whole release | redeploy the previous tag with `deploy.sh` (as for any release) | minutes |
| Worker init | remove `init: true`, redeploy (not recommended: zombies return) | minutes |

---

## 10. Operations runbook

```bash
# Who is being limited right now (API container of one environment)
docker compose logs --since 30m api | grep '"security"' | grep -E '"(refused|would_refuse|blocked)"'

# Active temporary blocks, lift one, block one by hand (max 1440 minutes, always audited)
docker compose exec api python -m app.cli security blocks
docker compose exec api python -m app.cli security unblock 203.0.113.7
docker compose exec api python -m app.cli security block 203.0.113.7 --minutes 60 --reason "ticket 123"

# Traffic classification / anomalies (read-only; --anonymize to share)
docker logs --since 1h esabong-gateway-gateway-1 2>&1 | python3 infrastructure/monitoring/traffic-report.py

# Host connection caps
sudo infrastructure/firewall/edge-guard.sh status | offenders | observe | enforce | rollback
```

Incorrect block reported by a user: unblock (above), add the address to `SECURITY_ALLOWLIST` if it
is an operational service, and raise the threshold only after reading the `would_refuse` lines.

---

## 11. Remaining risks and limitations

- **Volumetric DDoS** is not mitigated until Layer 1 (§5) is enabled; the VPS uplink is the limit.
- **WebRTC/RTMPS media ports** stay directly exposed (by design, key/token gated); see §5.3.
- **CGNAT**: limits are per address for anonymous traffic; a busy carrier NAT could reach the
  anonymous budget (600/min) before a person does. Signed-in traffic is per account. Watch
  `would_refuse` for `anon_per_minute` before tightening it.
- **IPv6**: the nftables edge guard covers IPv4 (DNS has A records only).
- **In-process concurrency caps** are per worker (×4 workers) by design; none are configured.
- **Host items needing owner approval** (not changed): `PermitRootLogin prohibit-password` or `no`,
  `X11Forwarding no`, load the host firewall table only after adding production's 1936/8189 (A12),
  apply the 10 pending package updates in a maintenance window. Membership of the `deploy` user in
  the `docker` group is equivalent to root on the host.
- **DevTools** cannot be disabled; anything in a bundle is public. Protection comes from server-side
  authorisation, not from hiding the client.
- Existing UAT zombies stay until the UAT worker is recreated (deploy, or `docker compose up -d
  --force-recreate worker` — needs approval).
