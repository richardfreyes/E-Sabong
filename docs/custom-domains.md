# Tenant custom domains

Owners manage each tenant at **Tenants → Domain Settings**. The default URL comes from
`BASE_DOMAIN` (`https://swcmain183.live` in Production). Custom domains are optional;
adding, disabling, editing or removing one does not change default-domain access,
accounts, invitation hierarchies, wallets or commissions.

## Connect a domain

1. Add the hostname or its HTTPS URL. Ports, paths, credentials and wildcards are refused.
   A hostname can belong to only one tenant, enforced by a case-insensitive database index.
2. Publish the unique `_esabong-verify.<hostname>` TXT value displayed in the dashboard.
3. For a subdomain, add a CNAME to the displayed environment's `BASE_DOMAIN`. For an apex,
   use A/AAAA records pointing to the public gateway. Remove old A/AAAA records; every
   resolved address must belong to this gateway. Turn off DNS-provider proxying during
   verification. Keep the ownership TXT record for subsequent health checks.
4. Click **Verify DNS / SSL**. Ownership and routing must both pass before the API serves
   the tenant or Caddy may issue a certificate. The first successful check commits DNS
   approval and leaves SSL pending; a second check makes a real TLS handshake and validates
   certificate trust, hostname and expiry. Caddy can also issue on the first browser visit.
5. Enable or disable the mapping as needed; **Make primary** marks the preferred domain.
   Editing the hostname rotates its ownership token and clears verification and SSL state.

Connection status is Pending (including certificate issuance), Verification Required,
Active, Failed or Disabled. SSL status and the last TLS check are displayed separately.
The hourly `domains.recheck_pending` worker checks all enabled custom domains, including
verified ones. Missing ownership or incorrect routing revokes approval; certificate
failures are displayed without removing default-domain access. Caddy handles issuance and
renewal; the application observes certificate health rather than claiming that DNS approval
alone means HTTPS is ready.

## Infrastructure and rollout

Apply migration **0036** using the existing environment migration procedure, then deploy
the API, worker, beat and all three portal builds. Reload the corresponding Caddy config.
Use the repository's usual backup and staged UAT release process before Production.

`CUSTOM_DOMAIN_GATEWAY_IPS` is an optional comma-separated list of public IPv4/IPv6 gateway
addresses, passed to API and workers through Compose. When empty, verification compares
custom A/AAAA records against the environment's `BASE_DOMAIN` A/AAAA records. Set it when
the base hostname uses a DNS proxy or resolves differently from direct custom-domain traffic.
Ports 80 and 443 must reach the gateway, CAA must permit Caddy's CA, and Caddy's `/data`
certificate storage must remain persistent. Public CA issuance requires publicly delegated DNS.

The single-stack Caddyfile uses the existing API TLS ask hook. The shared Contabo gateway
now checks Production and then UAT for both certificate approval and request routing.
Each environment keeps its own database, users, origins and media. Production takes priority
if operators verify the same hostname in both environments; use distinct UAT custom hostnames
and ownership records. The UAT-only gateway also supports dynamic tenant domains.
The loopback certificate broker on gateway port 8082 is not published. Origin port 8081
and `/api/v1/internal/*` must remain private. No manual per-domain Caddy entries are needed.

## Isolation and global video

On the shared default domain, existing session/invitation-based tenant resolution continues.
On a custom domain, the live enabled/verified hostname mapping determines the tenant before
authentication. Bearer tokens, refresh cookies, invitations and qualified usernames cannot
switch that mapping. Custom mappings are read from the database on every resolution, avoiding
stale cached ownership after disable, edit or reassignment. Domain writes lock their records
so a concurrent edit cannot inherit another hostname's DNS approval.

Access tokens still require the matching tenant ID. Tenant sessions retain the existing
PostgreSQL forced row-level security and explicit query filters. Refresh cookies are host-only,
HttpOnly, SameSite=Strict and Secure in HTTPS environments; browser sessions are independent
across domains. Referral links created from a custom domain stay on that origin.
Both the API and Caddy deny Owner routes on custom domains, including Owner login and requests
carrying a valid platform token. Owner access is restricted to `PLATFORM_HOST`. HTTPS edges also require the TLS server
name to match the HTTP Host, rejecting domain-fronting requests with status 421.

The existing Owner rental/global-arena configuration continues to select the shared stream
and control publishing. Its access permissions and viewer-balance checks apply unchanged
on custom domains. HLS and WebRTC signalling use relative `/stream/` URLs through the selected
environment's HTTPS gateway; no cross-domain cookie sharing is needed. Tenant users cannot
change the Owner's source or publish to it. Keep browser stream sources HTTPS and any external
provider origins in the existing `VIDEO_BROWSER_ORIGINS` allowlist.

## Validation

- `cd api && .venv/bin/python -m pytest tests/test_custom_domains.py tests/test_provisioning.py tests/test_root_tenant.py tests/test_isolation.py tests/test_shared_rental.py -q`
- `python3 scripts/test-custom-domain-gateway.py` (Docker; disposable Caddy and synthetic origins).
  Exercises the actual gateway handlers for Production/UAT selection, preservation of POST
  bodies and authentication, stream routing, Owner denial and disabled/unknown hosts.
- `cd web && npm run build:all`

The automated API tests mock public DNS and TLS outcomes. Before enabling a real customer
hostname, validate its live TXT/A/AAAA records and certificate from UAT/Production, log in and
refresh on that hostname, check another tenant's token is refused, check Owner routes return
404, and play the Owner stream with an eligible and an ineligible viewer. Public ACME issuance
and renewal cannot be established by local mocked DNS tests.

Caddy behavior follows its [on-demand TLS documentation](https://caddyserver.com/docs/automatic-https#on-demand-tls)
and [proxy response handlers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#intercepting-responses).

## Local verification — 2026-11-14

- Focused domain, provisioning, root-domain, RLS and shared-rental suite: **61 passed**.
- Separate current authentication, portal sessions, agent/sub-agent hierarchy, wallet,
  commission, video and utility regression suite: **106 passed**.
- Owner, Admin and Player production builds: passed (existing Player stylesheet budget warnings).
- Caddy validation: shared gateway, UAT-only gateway and single-stack edge passed.
- Disposable gateway proxy smoke test: passed.
- The exploratory full `tests unit_tests` run was not clean: **775 passed, 60 failed** at that
  point. It collected legacy `* 2.py` copies and encountered older migration rollback errors,
  unresolved legacy wagers contaminating shared-rental activation tests, and configuration
  environment contamination. Two domain-related expectations from that run were subsequently
  updated for strict hostname validation and the Owner-host 404 response. The focused and
  separate current regression suites above were rerun successfully. This is not a claim that
  the entire repository test suite passes.

These checks used a disposable local test database and synthetic stream origins. No UAT or
Production deployment, customer DNS change, or live public certificate issuance was performed.
