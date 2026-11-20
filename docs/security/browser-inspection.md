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
API, database, compose settings, gateway or existing Caddy configuration. Fetch and check out
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
  shortcuts were also sent through browser keyboard automation for Production and development.
- Clean isolated Production/UAT builds and existing bundle gates passed (135 deployed files).
  A development build emitted **32 source maps** and retained the Angular development banner;
  optimized outputs contained no backend `.py` files.
- Chromium ordinary-key dispatch, including event allocation: Production **0.0011–0.0012 ms/event**;
  development **0.0008–0.0012 ms/event**, 5,000 events per app. This is a small event microbenchmark,
  not an end-to-end performance guarantee. No periodic work is introduced.
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
