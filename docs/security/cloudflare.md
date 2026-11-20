# Cloudflare in front of swcmain183.live — audit, configuration and cutover plan

Status (2026-11-20): **prepared, nothing changed** in DNS, Cloudflare, the gateway, the firewall or the
applications. Every production-affecting step below needs explicit approval and a window without a
live event. Companion documents: [README](README.md) (edge guard, monitoring),
[gateway reload plan](gateway-reload-plan.md).

## 1. Audit findings (read-only, 2026-11-20)

| # | Finding | Impact on the Cloudflare plan |
|---|---------|-------------------------------|
| C1 | Nameservers are **GoDaddy** (`ns53/ns54.domaincontrol.com`); registry NS TTL 1 h; no DNSSEC (no DS record) | Plain NS switch, no DNSSEC migration. Cloudflare's assigned nameservers and zone status are only visible in the Cloudflare dashboard (no account access here) |
| C2 | Public records: `A @` and `A uat` → 46.250.249.61 (TTL 600), `CNAME www` → apex (3600), `TXT _dmarc` (DMARC quarantine). No MX, SPF, CAA, AAAA, wildcard | Few records; anything not discoverable from outside (GoDaddy-only records) must come from a **GoDaddy zone export** before the switch |
| C3 | Origin certificates: Let's Encrypt for `swcmain183.live` and `uat.swcmain183.live`, valid to 2027-02-01/02 | **Full (strict)** works as is |
| C4 | `www.swcmain183.live` resolves but **fails TLS today** (no gateway site, no certificate) | Pre-existing; fix at the Cloudflare edge with a redirect rule (§3) |
| C5 | Production has **one verified external custom domain** pointing straight at 46.250.249.61 (A record), served with an on-demand certificate; plus 2 in-zone tenant subdomains whose SSL is pending (no DNS for them). UAT: 2 pending in-zone subdomains | That customer's traffic and certificate renewals never pass Cloudflare → **the origin cannot be locked to Cloudflare** while it exists (§7) |
| C6 | **Custom-domain re-check (hourly at :15)** compares the customer's A records with the platform's gateway IPs; `CUSTOM_DOMAIN_GATEWAY_IPS` is **unset** in both environments, so it uses `BASE_DOMAIN`'s A records | Once the apex is proxied those are Cloudflare's → the customer domain is marked FAILED within an hour. **Prerequisite:** set `CUSTOM_DOMAIN_GATEWAY_IPS=46.250.249.61` in both `.env` files (config only; API/worker restart) |
| C7 | Production OBS ingest URL is `rtmps://swcmain183.live:1936` (public 1936, Let's Encrypt cert copied from the apex). Today's broadcast publishes over **WHIP** (HTTPS signalling + UDP media); RTMPS use before today's restart cannot be seen | If OBS RTMPS is used, the apex can no longer carry it once proxied → DNS-only `ingest` hostname + certificate, or WHIP only (§6) |
| C8 | WebRTC: MediaMTX advertises **46.250.249.61** (`STREAM_PUBLIC_HOSTS`), UDP/TCP 8189 (prod) and 8190 (UAT) public | Unaffected by proxying, but every viewer learns the origin IP (§6) |
| C9 | Host has a **global IPv6** address (no AAAA in DNS); docker-proxy accepts 80/443 on IPv6 | Second bypass path; covered by the origin lock (§7) |
| C10 | Public listeners: 22, 80, 443, 1936, 8189, 8190 (TCP), 8189, 8190 (UDP); PostgreSQL/Redis/1935/1945/1946 loopback or unpublished. Host firewall table `esabong_host` not loaded | §7 |
| C11 | Gateway (separate checkout `/opt/esabong/gateway`) runs the 2026-11-05 config (294eaf5) with **no proxy trust**: a client's `X-Forwarded-For` is replaced by the socket address | Behind Cloudflare, without the trust change every visitor would appear as a Cloudflare address: production's guard (monitor) would log wrong addresses, **UAT's enforce mode and the sign-in IP lockout (50 failures/15 min per address) would hit all users sharing a Cloudflare edge** → gateway trust must be live **before** any record is proxied |
| C12 | Edge guard: production `monitor`, UAT `enforce`; per-account budgets for signed-in users, per-address budgets for anonymous ones | Correct once the gateway restores the real client address |
| C13 | Production was live during the audit (event, desk, stream); this plan never assumes otherwise | Every disruptive step is scheduled outside live events |

## 2. DNS records

Current (public view) and proposed (Cloudflare zone). Cloudflare proxied records have an automatic
5-minute TTL; DNS-only records keep the TTL set here.

| Type | Name | Content | Today | Cloudflare: at NS switch | Cloudflare: after validation |
|------|------|---------|-------|--------------------------|------------------------------|
| A | `@` | 46.250.249.61 | direct | **DNS only** (grey) | **Proxied** (production cutover) |
| A | `uat` | 46.250.249.61 | direct | **DNS only** | **Proxied** (UAT validation, first) |
| CNAME | `www` | `swcmain183.live` | broken TLS | DNS only | Proxied + redirect rule to `https://swcmain183.live` (fixes C4; needs approval) |
| TXT | `_dmarc` | existing value, unchanged | | same | same |
| A | `ingest` | 46.250.249.61 | — | — | **DNS only**, only if OBS RTMPS is kept (§6) |
| … | anything else in the GoDaddy export | unchanged | | same, DNS only | review one by one |

Rules: no duplicates (one A per name), no wildcard (tenant subdomains stay off until needed; a
proxied wildcard is possible later), keep GoDaddy's zone untouched until the cutover is accepted (it
is the instant rollback target). No DNSSEC today; if enabled later, enable it in Cloudflare first and
add the DS record at GoDaddy only after the NS switch.

## 3. SSL/TLS and edge settings

| Setting | Value | Why |
|---------|-------|-----|
| SSL/TLS mode | **Full (strict)** | Origin has valid Let's Encrypt certificates (C3); never Flexible (redirect loop with the gateway's HTTP→HTTPS) |
| Edge certificate | Universal SSL (apex + `*.swcmain183.live`, covers `uat`) | Free; one level only (no `*.uat`) |
| Always Use HTTPS | **Off** at first | The gateway already redirects; keeping Cloudflare out of `:80` guarantees Let's Encrypt HTTP-01 renewals reach the gateway unmodified. Revisit after one renewal |
| Automatic HTTPS Rewrites, Rocket Loader, Email Obfuscation, Mirage/Polish, Always Online | **Off** | They rewrite HTML/JS (UI change) and conflict with the CSP `script-src 'self'` |
| Minimum TLS | 1.2; TLS 1.3 on | Same as today's browsers |
| 0-RTT | **Off** | Replayable early data must never reach bet/declaration POSTs |
| HSTS (Cloudflare) | Off | The origin already sends `max-age=31536000; includeSubDomains`, passed through unchanged |
| WebSockets | On | `/api/ws`, `/api/ws/simulations` |
| HTTP/3 (edge) | On | Clients ↔ Cloudflare only; Cloudflare ↔ gateway stays HTTP/1.1/2 |
| Cache Rules | **Bypass** for `/api/*`, `/stream/*`, `/index.html`, `/` | No API response, playlist or HTML shell may ever be cached; hashed bundles (`immutable`) are cached by origin headers |
| Browser Cache TTL | Respect existing headers | |

## 4. DDoS and WAF (conservative start)

| Control | Setting | Notes |
|---------|---------|-------|
| HTTP DDoS managed ruleset | On (always on), default sensitivity | Review Security Events after cutover; lower a rule only for a proven false positive |
| Under Attack mode | **Off** | Only during an active attack, and never longer than needed (it challenges XHR/WebSocket clients) |
| Security Level | Low | Shared carrier NAT addresses often have poor reputation; challenges break XHR |
| Bot Fight Mode (Free) | **Off** | Cannot be skipped per path on Free; would challenge API/WebSocket clients |
| Super Bot Fight Mode (Pro+) | definitely automated → managed challenge on HTML pages only; verified bots allow; static resource protection off | Skip for `/api/*`, `/stream/*` |
| Browser Integrity Check | On, skipped for `/api/ws*` and `/stream/*` | |
| Managed WAF rules | Free managed ruleset (automatic); on Pro: Cloudflare Managed + OWASP at default paranoia, reviewed in Security Events for a week before anything is tightened | |
| Custom rule 1 — scanner paths | `ends_with(path, ".php") or starts_with(path, "/wp-") or contains(path, "/.env") or contains(path, "/.git")` → **Block** | Matches the 25 scanner sources seen in 72 h; no portal route looks like this |
| Custom rule 2 — real-time skip | `starts_with(path, "/api/ws") or starts_with(path, "/stream/")` → **Skip** (Security Level, Browser Integrity Check, bot products) | WebSocket handshakes and HLS/WHEP must never be challenged |
| Rate-limit rule (Free: 1) | `POST` to `/api/v1/auth/login` or `/api/v1/platform/auth/login`: > 30 requests / 10 s per IP → Block 10 s | ≈ 25× the busiest address (baseline 13 auth/min); CGNAT-safe |
| Rate-limit rule (Pro: 2nd) | `/api/*` except `/api/ws*`: > 600 requests / 10 s per IP → Block 10 s | 60/s ≈ 5× the busiest address's peak second; real per-account limits stay in the API |
| Country / ISP blocks, IP allow-only rules | **None** | Globe, Smart, DITO, PLDT, Converge and every other provider keep working; no CAPTCHA by default |

The API's own guard stays authoritative for application limits (per account, critical routes never
refused); Cloudflare absorbs volumetric and obviously malicious traffic.

## 5. Gateway (Caddy) changes

Prepared and tested, **not applied**:

1. `infrastructure/gateway/Caddyfile.candidate-20261120` = the file running today (294eaf5) + only:
   `read_header 15s`, `max_header_size 64KB`, `protocols h1 h2`,
   `trusted_proxies static {$GATEWAY_TRUSTED_PROXIES}`, `client_ip_headers {$GATEWAY_CLIENT_IP_HEADER}`,
   one verified `X-Forwarded-For {client_ip}` to the origins (drop `X-Real-IP`/`Forwarded`), no
   `Server`/`Via`, 16 MB body ceiling, owner allowlist on `client_ip`. Routing (including custom
   domains) is exactly today's.
2. `entrypoint.sh` and `docker-compose.yml` from `main` (they differ from the running ones only by
   the two trust variables and their validation: no `0.0.0.0/0`, no private ranges, known header).
3. Gateway `.env`: `GATEWAY_TRUSTED_PROXIES=$(infrastructure/gateway/cloudflare-ips.sh)` (Cloudflare's
   published ranges, validated) and `GATEWAY_CLIENT_IP_HEADER=CF-Connecting-IP`.
4. Apply with `gateway.sh up` (the container must get the new environment): **~1–3 s without
   listeners on 80/443 for both environments**, every WebSocket reconnects once.

Behaviour (tested, `scripts/test-edge-hardening.py`, 36/36 for both the candidate and `main`'s file):
from a Cloudflare address the visitor address is taken from `CF-Connecting-IP`; from anywhere else
(direct clients, the custom domain) the socket address is used and forged `CF-Connecting-IP` /
`X-Forwarded-For` / `X-Real-IP` / `Forwarded` headers are discarded; the origin and API see exactly
`<visitor>, <gateway>`; uvicorn resolves the visitor; custom domains keep their on-demand routing.
Updating the ranges later: `cloudflare-ips.sh --check` (exit 1 when Cloudflare's list changed), then
update `.env` and `gateway.sh up`. Applying the trust before any record is proxied is harmless (no
Cloudflare address connects yet), so it goes first.

No change to the per-environment origin Caddy, the API, MediaMTX or any business code.

## 6. Streaming compatibility

| Path | Through Cloudflare? | Assessment |
|------|---------------------|------------|
| LL-HLS `/stream/hls/*` (same origin as the portals) | Yes, proxied, **cache bypass** | Works over HTTP; one extra edge hop (Manila/Singapore PoP). **Terms risk:** Cloudflare's self-serve plans restrict serving video through the CDN outside Stream/R2; serving it uncached still passes video through Cloudflare. Owner decision; keeping URLs unchanged rules out a separate DNS-only stream hostname without an app change |
| WHEP/WHIP signalling `/stream/webrtc/*` | Yes | Small HTTPS POSTs; fine |
| WebRTC media UDP/TCP 8189 (prod), 8190 (UAT) | **No** (direct to 46.250.249.61) | Unchanged quality and latency. Not protected by Cloudflare; the origin IP is visible in every viewer's ICE candidates |
| RTMPS ingest 1936 (`rtmps://swcmain183.live:1936`) | **No** — breaks if the apex is proxied | Options: (a) broadcasters use WHIP (already used today); (b) DNS-only `ingest.swcmain183.live` + a certificate for it + `STREAM_RTMPS_URL` change (config; OBS server URL changes once) |
| UAT RTMP/RTMPS | Loopback only | No change |

Longer-term streaming protection (separate decision): move WebRTC media to a relay/SFU edge
(Cloudflare Realtime/TURN via the existing `STREAM_TURN_URLS`/`STREAM_TURN_SECRET` settings, or a
DDoS-protected streaming host) so the main VPS address is no longer published to viewers.

## 7. Contabo origin protection (recommendations; nothing activated)

1. **Origin lock** (`infrastructure/firewall/esabong-origin-lock.nft` + `origin-lock.sh`, tested in an
   Ubuntu 24.04 namespace): 80/443 (IPv4 forward path and IPv6 input) only from Cloudflare's ranges
   plus an admin/monitoring allowlist; `apply` = observe (count direct sources), `enforce` = drop,
   `observe`/`rollback` undo at once. **Blocked by C5:** enforcing would cut the external custom domain.
   Options: give custom domains a second Contabo IPv4 (direct, not locked) while the main IP is
   locked; or Cloudflare for SaaS (needs a change to the app's custom-domain verification, which
   expects the gateway's A record — out of scope now); or accept no lock.
2. Keep open: SSH 22 from administrator addresses only (host table `admin_v4`), 1936 (if RTMPS
   kept), 8189/8190 TCP+UDP. Keep closed: 5432, 6379, 1935/1945/1946 (already loopback/unpublished).
3. Host table `esabong-host.nft` (not loaded): before ever loading it, add production's 1936 and
   8189 to its public sets (C10) — as committed it would cut production streaming.
4. The nftables edge guard (per-address connection caps) must exempt Cloudflare's ranges once
   proxied (`edge-guard.sh exempt` with `cloudflare-ips.sh --nft4`), or every visitor behind one edge
   shares a cap.
5. Optional later: Cloudflare Authenticated Origin Pulls (mTLS from Cloudflare to the gateway) for the
   proxied hostnames — protects 443 even where the IP filter cannot (needs a gateway change).

## 8. Tests done in this preparation

| Test | Result |
|------|--------|
| Gateway candidate + `main` file, real entrypoint, real origin, echo API (`scripts/test-edge-hardening.py`) | **36/36** each: Cloudflare trust (CF-Connecting-IP only from trusted ranges), forged headers discarded, custom-domain routing, WebSocket/HLS/WHEP pass-through, slow headers cut at 15 s, 431/413 limits, banners removed, owner allowlist |
| `cloudflare-ips.sh` against Cloudflare's API | 15 IPv4 + 7 IPv6 ranges, validated (global, prefix ≥ 8) |
| Origin lock lifecycle (Ubuntu 24.04, nft 1.0.9): check, apply (ranges loaded), allow, enforce (idempotent), refresh, observe, rollback; other tables still parse | Pass |
| Live Cloudflare behaviour (WAF, rate limits, edge TLS, real client IP via Cloudflare) | **Not yet possible**: the zone is not active (nameservers still GoDaddy). Done in phase 5 on UAT first |

## 9. Cutover plan (each step needs approval; no live event)

| Phase | Step | Disruption |
|-------|------|------------|
| A. Preparation | GoDaddy zone export; Cloudflare plan tier; create records **all DNS-only**; apply §3/§4 settings (they only act on proxied records); decide C5 (custom domain) and C7 (RTMPS) | None |
| B. App config | `CUSTOM_DOMAIN_GATEWAY_IPS=46.250.249.61` in prod and UAT `.env`, `docker compose up -d api worker` in each; run the custom-domain check once and confirm it stays verified | API/worker restart (~30–60 s) per environment |
| C. Gateway | Back up the gateway directory; apply §5 (candidate + entrypoint + compose + `.env` trust); `gateway.sh validate`; `gateway.sh up`; prod/UAT smoke tests (gateway plan) | ~1–3 s on 80/443 for both; WebSockets reconnect once |
| D. Nameservers | Set Cloudflare's two assigned nameservers at GoDaddy; records still DNS-only, so traffic still goes direct. Watch `dig NS` on several resolvers until all answer Cloudflare (≤ 1 h registry TTL, some resolvers longer) | None expected |
| E. UAT validation | Proxy `uat`; run the UAT suite (declarator workflow spec, payout spec, guard live test, HLS/WHEP check, `verify-declarator-uat.py`), confirm API logs show real client addresses (not Cloudflare's), Security Events clean, 24 h soak | UAT only |
| F. Production | Window without an event: proxy `@` (and `www` + redirect rule); smoke tests; real-user monitoring 60 min + 24 h; `traffic-report.py`; Cloudflare Security Events | Seconds (DNS change propagates within the 5-min proxied TTL; both paths work meanwhile) |
| G. Origin | `origin-lock.sh apply` (observe) for a week; enforce only after C5 is resolved | None in observe |

## 10. Rollback

| Scope | Action | Time |
|-------|--------|------|
| One hostname misbehaves behind Cloudflare | Set its record to **DNS only** (grey cloud) | Minutes (5-min TTL); traffic goes direct exactly as today |
| A WAF/rate rule blocks real users | Disable that rule (Security Events shows which) | Immediate |
| Cloudflare as a whole | Nameservers back to `ns53.domaincontrol.com` / `ns54.domaincontrol.com` (GoDaddy zone kept unchanged) | Up to the 1 h registry TTL (+ resolver caches) |
| Gateway trust change | Restore the backed-up `Caddyfile`/`entrypoint.sh`/`docker-compose.yml`/`.env`, `gateway.sh up` | ~1–3 s |
| Custom-domain setting | Remove `CUSTOM_DOMAIN_GATEWAY_IPS` (only after un-proxying the apex) and restart api/worker | ~30–60 s |
| Origin lock | `origin-lock.sh observe` or `rollback` | Immediate |

## 11. Remaining risks

- **Origin IP is public** (DNS history, WebRTC ICE candidates, the custom domain): attackers can
  bypass Cloudflare until the origin lock is enforced, and the streaming ports can be flooded directly.
- **External custom domain** prevents the origin lock (C5) without a second IP or an app change.
- **HLS via Cloudflare** has a terms-of-service risk on self-serve plans (§6).
- **RTMPS ingest** URL must change if OBS RTMPS is used (C7).
- **Free plan limits**: 1 rate-limit rule, no regex in rules, sampled Security Events only; a Pro plan
  gives the second rate-limit rule, managed WAF and Super Bot Fight Mode.
- **Cloudflare outages / edge restarts** close WebSockets occasionally; the portals reconnect.
- **Let's Encrypt renewals through Cloudflare**: HTTP-01 must keep reaching the gateway (Always Use
  HTTPS off); first renewal ≈ early January 2027 — check it, or switch the proxied hostnames to a
  Cloudflare Origin CA certificate.
- No Cloudflare setting has been applied or observed yet; every claim about Cloudflare's behaviour
  in this document is to be confirmed on UAT in phase E.

## 12. Status 2026-11-20 06:15 UTC (DDoS incident)

- **Origin 46.250.249.61 unreachable** from 05:59 UTC on every port incl. ICMP and SSH; neighbouring
  Contabo addresses answer and the route to `.61` dies before Contabo's upstream (Hurricane Electric):
  consistent with a provider null-route. Cloudflare cannot reach a null-routed origin either.
- **`swcmain185.live`** is production's verified **custom domain** (same VPS, GoDaddy `ns35/ns36`,
  `A @` → 46.250.249.61, `www` CNAME, `_dmarc`, ownership TXT `_esabong-verify`).
- **Cloudflare zones created** (account `Esabongadmin@gmail.com's Account`, Free plan, `pending`):
  `swcmain183.live` (7a3133109163de334bf0ebf3f4c7c563) and `swcmain185.live` (072004066199baa0e23dc56dcb069027),
  nameservers **collins.ns.cloudflare.com / nero.ns.cloudflare.com**. Records mirrored DNS-only;
  parity with GoDaddy verified over 363 name/type pairs per zone (only `_domainconnect`, a provider
  pointer, differs). Settings and rules of §3/§4 applied (cache bypass, real-time skip, scanner block,
  sign-in rate limit; DDoS L7 + Cloudflare Managed Free Ruleset; Bot Fight Mode off).
- **Proxying `swcmain185.live`** needs its Cloudflare anycast addresses in `CUSTOM_DOMAIN_GATEWAY_IPS`
  (the hourly check compares the domain's A/AAAA with that list): proxy right after a :15 check, read
  the addresses, run `cloudflare-origin.sh app-config "46.250.249.61,<cf v4>,<cf v4>,<cf v6>,<cf v6>"`
  before the next :15.
- Origin steps scripted: `infrastructure/scripts/cloudflare-origin.sh` (backup, app-config, gateway,
  verify, rollback-gateway, rollback-app).

## 13. Activation 2026-11-20 (incident)

| Time (UTC) | Step |
|------------|------|
| 05:59–06:48 | Origin unreachable (null-routed); restored by the provider at ~06:48, no reboot (uptime 6 days) |
| 06:33–06:34 | GoDaddy nameservers → collins/nero.ns.cloudflare.com (gddy CLI, operations aca71bd0…, 36a9cc3a…), registry updated ~06:44 |
| 06:44 / 06:46 | Zones active: swcmain183.live / swcmain185.live; Universal SSL active for apex + wildcard |
| 06:49 | Backup `~/esb-jobs/cf-backup-20261120T064937Z` (both .env, gateway files, running Caddyfile) |
| 06:50–06:53 | `CUSTOM_DOMAIN_GATEWAY_IPS=46.250.249.61` (prod + UAT, api/worker recreated; ~66 s of 502/503 on prod during the restart) |
| 06:54 | Gateway: candidate + Cloudflare trust (`CF-Connecting-IP`, 22 published ranges); verify passed (3 hosts × portals/API/WS 101; forged headers ignored) |
| 06:56 | `uat` proxied → validated (real client IP, WS 101, scanner paths 403 at the edge) |
| 06:57 | `@` swcmain183.live proxied → validated |
| 06:58 | `@` swcmain185.live proxied; its Cloudflare addresses added to `CUSTOM_DOMAIN_GATEWAY_IPS` (06:58–07:02, before the 07:15 check) → `custom verified active` |

Validation through Cloudflare (UAT, tests pinned to Cloudflare's address): declarator workflow spec 12 cycles / 121 bets, 0 failures, both tenants balanced, 0 leaked frames; LL-HLS 328/328 × 200, media advancing; guard live test 1,150 legitimate requests 0 × 429, anonymous burst limited per real client IP (609 served / 91 refused), WebSocket flood 1008 + reconnect; 50,375 protected UAT rows unchanged. Production fingerprint 03:31→07:19: no existing bet/wallet-entry/commission/result row changed, both tenants balanced.

Side effect of the outage (not of the cutover): the failed custom-domain check during the null-route cleared `is_primary` on swcmain185.live (set by the owner 11-14); re-set it in the owner console.

Still direct (not protected by Cloudflare): `www` CNAMEs (DNS-only; www had no origin certificate before), WebRTC media 8189/8190, RTMPS 1936, the IP itself (public in DNS history and ICE candidates).
