# Browser inspection deterrents

These controls discourage casual inspection only. Browser menus, extensions, remote debugging,
JavaScript disabled before boot, browser-reserved shortcuts, and the editing/accessibility
exemptions can bypass them. They are not an authorization boundary. Nothing in this change
modifies betting, odds, payouts, balances, commissions, APIs, livestreaming, or WebSockets.

## Files and environment configuration

| File | Purpose |
| --- | --- |
| `web/shared/services/browser-inspection.service.ts` | Shared service, two document event listeners, explicit DI flag, cleanup on application destruction |
| `web/shared/services/providers.ts` | Start once through the existing shared provider in Owner, Admin and Player |
| `web/shared/config/environment.ts` | Local default: `inspectionDeterrents: false` |
| `web/shared/config/environment.production.ts` | Production: `inspectionDeterrents: true` |
| `web/shared/config/environment.uat.ts` | UAT: `inspectionDeterrents: true` |
| `web/angular.json` | Explicit production/UAT replacements, optimized scripts/styles, no maps; development retains maps and debugging |
| `web/package.json` | `build:uat` builds all three apps with the UAT configuration |
| `infrastructure/docker/web.Dockerfile` | `WEB_CONFIGURATION=production` by default; accepts only `production` or `uat`, then runs the existing bundle gate |
| `web/shared/services/browser-inspection.service.spec.ts` | Shortcut, modifier, editing, selection, cleanup and all three build-configuration tests |
| `web/e2e/inspection-deterrents.spec.ts` | Browser events, form interactions, native pointer right-click, debug hooks and event dispatch measurement |
| `web/playwright.inspection.config.ts` | Separate local build matrix or gated remote UAT validation |
| `web/playwright.config.ts` | Exclude the separate build-matrix suite from the normal development suite |
| `infrastructure/caddy/Caddyfile.origin` | Strip the existing Owner prefix before the SPA rewrite so its JavaScript/CSS/assets are actually served |
| `scripts/test-owner-static.py` | Disposable origin regression test for portal assets, deep links, host isolation and map/hidden-file rejection |

The Admin app includes Declarator and agent routes; Master Agent and Agent workspaces in the
Player app also receive the same provider. There are no per-route or per-role opt-outs.
Configuration is compiled into the bundle and never inferred from a hostname. A production
build served on localhost deliberately retains deterrents. UAT also remains protected when the
existing image pipeline uses its default optimized `production` configuration.

`ng serve` and `npm run start:{owner,admin,player}` still select development by default. The local
service installs **zero** listeners. Frontend environment files contain only public booleans;
server `.env` files and keys must never be imported into frontend configuration.

## Browser behavior and accessibility

The service cancels the default action for unmodified F12, Ctrl+Shift+I/J/C and
Cmd+Option+I/J/C, only when the browser delivers a cancelable event to the page. It recognizes
physical key codes as well as characters (Option can change the character on macOS). Other
modifiers, IME composition and AltGraph input are left alone. Handled application events are
respected, and event propagation is never stopped.

Pointer context menus are canceled on ordinary content, including macOS Ctrl-click. Editing
controls, contenteditable regions (including nested/shadow content), textbox/searchbox/combobox
roles, selected text and native audio/video controls retain their menus. Keyboard context menus,
Context Menu key and Shift+F10 remain available. There is no existing application context-menu
implementation or Inspect command to remove; this only deters the browser's pointer menu where
supported. Exempted native menus can still contain Inspect.

Firefox can show its native menu on Shift+right-click without sending `contextmenu` to the page.
Browser/OS/assistive technology may consume or override shortcuts. Native menus cannot be selectively
edited to remove just Inspect. See [MDN contextmenu documentation](https://developer.mozilla.org/en-US/docs/Web/API/Element/contextmenu_event).
Only Chromium was exercised here; native browser chrome and assistive technologies require manual
validation in the browsers used by the deployment.

There is no DevTools-open detection, debugger statement, timer, polling, console replacement,
reload, logout, DOM/style change, network request, or background loop in the service. Two bubble
listeners run outside Angular; ordinary typing does not trigger change detection through them.

## Build and backend protections

Production and UAT build configuration explicitly enables JavaScript optimization and CSS
minification, sets `sourceMap: false`, and keeps hashed bundles. Angular's optimized build removes
development mode; browser tests verify Angular debug hooks are unavailable. Development retains
them. [Angular documents build replacements and configuration](https://angular.dev/tools/cli/environments).

The existing `web/scripts/verify-prod-bundle.mjs` runs inside the image build and rejects map
files/references, Angular's development banner, known secret-value patterns, credential URLs,
private keys, local paths and Finder duplicates. `.dockerignore` excludes server environment
files; the final Caddy stage copies only portal outputs and Caddy configuration. No API/backend
source is copied into its public roots. The existing origin rejects source map and hidden-file
probes. A pattern scan cannot certify the absence of every possible secret; browser assets must
always be treated as public.

Server-side authentication, role/permission checks, tenant scoping and revocation remain in
`api/app/core/deps.py` and the protected routers. Their regression tests run without any browser
deterrent. Bypassing this service never grants a token, permission or data access.

## Reproduce checks

From `web/`:

```sh
npm run test:unit
npm run build:all
node scripts/verify-prod-bundle.mjs dist/owner dist/admin dist/player
npm run build:uat
node scripts/verify-prod-bundle.mjs dist/owner dist/admin dist/player
INSPECTION_CONFIGURATION=production npx playwright test -c playwright.inspection.config.ts
INSPECTION_CONFIGURATION=uat npx playwright test -c playwright.inspection.config.ts
INSPECTION_CONFIGURATION=development npx playwright test -c playwright.inspection.config.ts
npm run e2e
```

The normal E2E suite needs the local API, PostgreSQL and Redis; its existing global setup creates
a fresh synthetic tenant. From `api/`: `.venv/bin/python -m pytest tests unit_tests -q` uses the
separate disposable `esabong_test` database and Redis databases 14/15.

## Deployment and rollback

Use the dedicated `feat/browser-inspection-deterrents` branch. UAT validation is authorized;
Production deployment requires a subsequent explicit approval and successful UAT sign-off.
There are no migrations or runtime server secret/config changes.

For a frontend-only release, in the intended clean environment checkout, first record the
previous Caddy image ID and tag it as a rollback image. Verify `ESB_STACK` is `uat`, the Compose
project is `esabong-uat`, the checkout is `/opt/esabong/uat`, and no source change reaches the
API, database, compose settings or shared gateway. Review the small Owner static-prefix fix
separately; it preserves the existing host restriction and `/owner/` routes. Fetch and check out
the reviewed release commit, then:

```sh
docker compose build --build-arg WEB_CONFIGURATION=uat caddy
docker compose up -d --no-deps caddy
```

Only the UAT origin container is recreated. The origin restart can briefly interrupt UAT HTTP,
media requests or existing proxied sockets; normal client reconnection handles this. No backend,
worker, database, MediaMTX or shared gateway restart is needed. Check container health, portal
and API responses, source-map/secret probes, and the gated browser suite:

```sh
INSPECTION_REMOTE_URL=https://uat.swcmain183.live INSPECTION_CONFIGURATION=uat \
  npx playwright test -c playwright.inspection.config.ts
```

Supply `E2E_CONTABO_ACCESS_TOKEN` securely through the process environment; never put its value
in command history, a URL, screenshots, test traces or this document. The suite uses only public
login pages and makes no financial writes. Separately validate authenticated role workspaces
and any required native browser/accessibility combinations before Production sign-off.

After approval, repeat the frontend-only steps in `/opt/esabong/prod`, verify project
`esabong-prod`, and use `--build-arg WEB_CONFIGURATION=production`. Do not run this step until
explicitly approved. Keep the shared gateway and every backend service untouched.

Rollback: retag the saved previous image to the environment's Caddy image name and run
`docker compose up -d --no-deps caddy`; return the checkout to its previous revision. No database
restore or migration is involved. Removing the deterrent alone requires rebuilding with the
public flag disabled in a reviewed release, not a hostname exception.

## Validation results

Local results, 2026-11-20:

- Frontend unit suites: **435 passed** (136 Owner/shared, 179 Admin, 120 Player), including 29
  focused inspection/configuration cases.
- Backend integration/unit suites: **648 passed**, including authentication and permission tests.
- Production, UAT and development browser-policy checks: three apps each; all passed. All seven
  shortcuts were also sent through browser keyboard automation in all three configurations.
- Clean isolated Production/UAT builds and existing bundle gates passed (135 deployed files).
  A development build emitted **32 source maps** and retained the Angular development banner;
  optimized outputs contained no backend `.py` files.
- Chromium ordinary-key dispatch, including event allocation: Production **0.0011–0.0012 ms/event**;
  UAT **0.0012–0.0016 ms/event**; development **0.0008–0.0012 ms/event**, 5,000 events per app.
  This is a small event microbenchmark, not an end-to-end performance guarantee. No periodic work is introduced.
- Existing component style-budget warnings remain; no CSS was changed.
- The full pre-existing local browser suite initially produced **23 passed, 10 failed, 9 skipped,
  6 blocked by serial/setup failures**. Failures include legacy fight lifecycle/declarator calls,
  local Owner sign-in assumptions, balance selectors, a 768px overflow assertion and video setup.
  With the original startup provider restored (no inspection-service import or initializer),
  the baseline reproduced **all the same 10 failures**, plus one predictions synchronization
  failure: **20 passed, 11 failed, 9 skipped, 8 blocked**. The service was then restored.
  These existing suite failures remain unresolved under the restriction on business/UI changes;
  a fully green end-to-end release gate is **not** claimed.

- Existing bundle-gate negative controls also rejected a `.map` file, a source-map reference
  and a synthetic secret value (3/3).

Baseline failures reproduced with the original startup provider:

| Existing spec | Observed failure |
| --- | --- |
| `arena-responsive.spec.ts` | 768px content overflow: 782px versus the 769px assertion limit |
| `balance-management.spec.ts` | 60-second timeout; cleanup request sees a closed context |
| `betting.spec.ts`, `sub-agents.spec.ts` | Setup opens betting before starting the fight; server correctly returns 409 `fight_not_started` |
| `declarator-betting.spec.ts`, `declarator.spec.ts` | Direct test calls omit the current desk-version header; existing server guard returns 426 |
| `multi-portal-sessions.spec.ts`, `owner.spec.ts` (two cases) | Local Owner sign-in/heading or error assertions fail |
| `video-provider.spec.ts` | Declarator-assignment setup fails, also subject to the existing desk-version guard |

The baseline's additional `predictions.spec.ts` failure waited for a missing fight heading. It
passed with the change in the initial run. These results identify baseline defects/setup issues;
they do not establish that all application flows passed. No expectations were weakened.


## Owner origin routing prerequisite

Remote UAT validation exposed a pre-existing origin configuration defect: `uri strip_prefix
/owner` executed after the SPA snippet's `rewrite`. Requests for existing `/owner/*.js`, CSS,
logos and `theme-init.js` therefore returned `index.html` with an HTML MIME type, leaving Owner
on its startup loader. This was reproduced directly inside the origin container, independently
of Cloudflare/browser caching and of the inspection service.

The minimal correction uses `handle_path /owner/*`, which strips the prefix before entering the
existing host check and SPA handlers. It preserves Owner host isolation, deep links, the `/owner`
redirect, hidden-file/map rejection, and the existing app/UI/routes. No API, media or WebSocket
route is modified. It is included only in the UAT frontend origin image; Production requires the
same explicit release approval as the deterrents.

`python3 scripts/test-owner-static.py` passed locally against disposable Caddy for all three
portals' JavaScript, CSS, logo assets, deep links and blocked file paths, plus Owner host isolation.
A negative control restored the original configuration temporarily: the test correctly failed
on Owner JavaScript returning HTML; the correction was then restored. Increasing the readiness
wait alone did not resolve the failure; that temporary test change was removed after diagnosis.

The existing gateway/origin hardening suite produced **34 passed checks and one failed
slow-header timing check** (2.0 seconds versus its expected 13–25 seconds), identically with the
corrected and original origin configuration. Source-map blocks, hidden-file rejection, security
headers, API/body limits, HLS/WHEP forwarding, WebSocket upgrade forwarding and host/IP isolation
checks passed. The unchanged gateway timing failure remains a separate baseline issue.

## UAT deployment result (2026-11-20)

- Running release: **`uat-inspection-20261120-rc.2`**, commit
  **`6ed49cef1b448e265829b862c3de1c705245344a`**, dedicated branch
  **`feat/browser-inspection-deterrents`**. The first candidate (`7a75133`, rc.1) exposed the existing
  Owner routing defect; rc.2 includes its tested correction.
- Only `esabong-uat-caddy-1` was recreated. The guarded deployments compared container IDs/images
  before and after: **Production, the shared gateway and every UAT backend remained unchanged**.
  No migration, financial write, API deployment, MediaMTX restart or server environment edit.
- UAT frontend image: `sha256:7c6938b7c3570990281fa5d6bbc345dd756ebaf1f5737ac05e77b5f9ad4be98f`;
  container health **healthy**. Image build gate passed for **135 files**. A scan of the running
  `/srv` roots found **zero** `.map`, `.py` or `.env*` files.
- **Three deployed-origin Chromium checks passed** in 17.2 seconds: Owner, Admin and Player.
  These include all seven browser-delivered shortcuts, pointer right-click, typing/editing,
  Tab navigation, editable/keyboard-menu exemptions and absent Angular development hooks.
  Verification used a temporary loopback SSH tunnel to the real TLS gateway with the real UAT
  hostname/certificate and UAT access cookie. No TLS validation or authentication was disabled.
- Public UAT smoke initially passed: portals/health/readiness/public tenant 200, file/source-map
  and docs probes 404, unauthenticated `/api/v1/admin/users` **401**. Subsequent direct HTTP
  automation received an edge **403**; origin checks and Chromium remained available.
- Public-CDN browser checks: **Admin and Player passed**. Owner remains **pending at the CDN**:
  Cloudflare retained HTML bodies for its asset URLs from the pre-existing routing defect, even
  after the corrected origin served JavaScript. Both five- and fifteen-second readiness waits
  showed this was not a timeout issue. Direct origin checks confirm correct JS MIME types.
- Attempted only a specific-URL purge for **UAT Owner** using the existing Wrangler connection;
  Cloudflare refused it with **401 / 10000 Authentication error**. No purge succeeded, no token
  permissions were changed, and no Production cache operation was attempted. Plugin discovery
  found no Cloudflare connector. The missing cache access was requested from the operator.

**Remaining UAT action:** use Cloudflare Custom Purge for hostname **`uat.swcmain183.live` only**
(or its `/owner/` asset URLs), then rerun the public remote suite above. The API connection needs
Cache Purge permission for the zone. Keep all credentials out of chat and repository files.
[Cloudflare documents targeted cache purges](https://developers.cloudflare.com/cache/how-to/purge-cache/).
A fully successful public-CDN UAT validation is **not** claimed until this passes. Authenticated
role-workspace/native-browser/accessibility sign-off and resolution of the documented baseline
suite failures also remain release acceptance items. **Production has not been deployed.**

Rollback images retained on the VPS:

- `esabong-uat-caddy:pre-inspection-20261120` — original UAT frontend before this task (original
  checkout `047f2f875d12502050dbad14fa4be15c2d28a6a6`; includes the old Owner routing defect).
- `esabong-uat-caddy:pre-inspection-20261120-rc2` — rc.1 before the routing correction.

For complete rollback, retag the original saved image as `esabong-uat-caddy:latest`, then run
`docker compose up -d --no-deps caddy` in `/opt/esabong/uat` and return the checkout to its original
revision. Purge affected **UAT-only** CDN assets if necessary. Do not restore databases. Deployment
logs and before/after container evidence are in `~/esb-jobs/inspection-20261120/` and
`~/esb-jobs/inspection-20261120-rc2/` on the VPS.
