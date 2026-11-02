# Portal UI/UX audits

## 2 November 2026 — play credits, plan pricing, re-verification

Captured by `web/scripts/ui-audit.cjs 2026-11-02` against the local dev servers and API
(browser automation; nothing hand-made). Results in [`2026-11-02/`](2026-11-02/):

- **34 main routes × 9 widths** (320, 375, 390, 430, 768, 1024, 1280, 1440, 1920) = 306
  measurements; **0** page or main-content horizontal overflow; **0** uncaught browser errors
  ([`inventory.json`](2026-11-02/inventory.json)). 102 full-page screenshots at 320/768/1440.
- New Playwright checks (`web/e2e/resilience.spec.ts`): 320px player pages never scroll
  sideways; Admin phone navigation opens and is usable.

| Change this round | Before (30 Oct capture) | After (2 Nov) |
|---|---|---|
| Play balances, pools, bets and returns shown as **play credits** (no ₱, "Odds" not "Payout", "Returns" not "Pays") | [`after/player-dashboard-320.png`](after/player-dashboard-320.png), [`after/admin-betting-1440.png`](after/admin-betting-1440.png) | [`2026-11-02/player-dashboard-320.png`](2026-11-02/player-dashboard-320.png), [`2026-11-02/admin-betting-1440.png`](2026-11-02/admin-betting-1440.png) |
| Owner plans: sale status (for sale / needs price approval / legacy USD review / retired), review banner, explicit price approval in the form | [`after/owner-plans-1440.png`](after/owner-plans-1440.png) | [`2026-11-02/owner-plans-1440.png`](2026-11-02/owner-plans-1440.png) |
| Declarator desk: credits wording for pools and returns by result | [`after/admin-declarator-1440.png`](after/admin-declarator-1440.png) | [`2026-11-02/admin-declarator-1440.png`](2026-11-02/admin-declarator-1440.png) |

Defects found by this round's checks and fixed: quick-amount chips announced 100× their value
to screen readers (caught by a unit test); the Owner review banner read "4 plans need review
before it can be sold". Not addressed: the phone header shows the play-credit balance as a bare
number (the unit is in its accessible name and tooltip, and in the betting panel).

---

## 30 October 2026 — continuation audit

This work continues the existing implementation in the working tree. The dark
theme, tenant branding, source badge fix, betting features, and provider integration
already existed at the start of this pass. Existing backend changes were preserved.
No backend implementation, schema, permission, route, or accounting changes were
added by this UI continuation.

Open [the screenshot comparison gallery](gallery.html) for before/after views,
or inspect [route measurements](after/inventory.json) and
[detail/dialog measurements](after/flows.json).

## Results

- 34 main routes inspected at **320, 375, 390, 430, 768, 1024, 1280, 1440 and 1920px**
  (306 measurements), plus 47 detail, dialog, menu, validation and tab states at
  320, 768 and 1440px (141 measurements).
- No page-level or main-content horizontal overflow or uncaught browser errors in
  the final main-route inventory. Tables and result boards keep their intentional
  internal horizontal scrolling.
- 45 live pool frames in the arena browser check: Meron/Wala positions remain
  aligned, the typed amount and control DOM survive, the video node survives,
  and no new provider requests occur. Decoded frames and elapsed video playback
  are also asserted.
- Real local-API tests verify cross-player pool updates, personal BET/PAYS,
  wallet changes, betting OPEN/CLOSED/SETTLED, result finalization, cancellations,
  permissions, branding, and licensed demo playback in Player and Declarator.

## Rendering defects and fixes

| Cause or defect | Change | Evidence |
| --- | --- | --- |
| Material tables used object identity; new API objects replaced rows | Stable id/match_id tracking, with a configurable row-key function | Shared table unit test asserts the original row survives replacement and reordering |
| A refresh error destroyed the loaded table | Keep rows mounted beside retry feedback | Unit test checks the row DOM survives a failed background refresh |
| A closing WebSocket could clear its replacement's socket, heartbeat and status | Socket ownership checks, pending-retry cancellation, and guarded refresh completion | Three reconnect-race unit tests |
| Equivalent playback metadata could reinitialize media | Compare source URL, type and expiry before notifying the playback effect | Video unit test; browser check retains video and counts provider requests |
| Superseded event, match, fight-book and report requests could apply late | Cancel superseded reads and unsubscribe with their owning lifecycle | Rapid-event-switch, no-selected-fight and report-ordering tests |
| Report selection changed headings before the corresponding data arrived | Keep the rendered report kind with the loaded response | Report unit test |
| Failed admin match refresh cleared the fight list | Preserve the fight cards and show retry feedback | Source review and event-detail browser verification |
| Global busy pseudo-element inserted an extra flex child into betting cards | Scope the spinner to Material buttons; animate the existing betting action icon | Betting control geometry and identity assertions |
| Large play balance pushed the account button outside 320px | Compact phone header spacing and responsive currency type | Long-value test initially reproduced 338px document width; final nine-width check passes |
| Playing video added enough controls to wrap the phone footer | Icon-only phone refresh and visually hidden status/label, retaining accessible text | Long-value arena screenshot and browser check with active playback |
| Fixed minimum grid tracks exceeded small parent widths | Constrain Player event and Admin fight/reconciliation grid minimums to their container | Route and settings/tab inventories |
| Pool text and card actions could lose alignment at narrow widths | Scale currency to card width and formatted length; preserve action height; allow supporting text to wrap | Nine-width geometry and clipping assertions with million-value figures |
| Skeleton/metric dimensions and hints differed during loading | Reserve metric/hint space and table placeholder row height | Dashboard and table visual comparisons |
| Validation text changed betting form height | Reserve help-text space | Open/closed/settled screenshots |
| Balance updates scaled the chip; ordinary metric hover moved cards | Retain a restrained balance highlight and border-only metric hover | Player/header and Owner/Admin captures |
| Pending event/plan submissions allowed repeated requests | Disabled busy submit buttons and handler guards; guards on tenant/account creation | Existing end-to-end form/lifecycle tests and browser validation captures |
| Phone navigation needed more accessible state information | Add explicit Player rail names and current-page indicators; preserve safe-area padding | Navigation/menu screenshots and source review |
| Expanded hierarchy names overlapped the balance column; late child responses could restore collapsed descendants | Separate identity/balance columns, compact mobile indentation, request guards and lifecycle cleanup | Expanded hierarchy capture and two race/duplicate-request unit tests |

## Audit steps and health

Each row below represents the same route at every requested width. “Pass” means
the captured loaded state rendered without page overflow or uncaught JavaScript
errors; it does not certify every possible data or permission state.

| Step | Portal and route | Health |
| --- | --- | --- |
| 1 | Owner overview `/` | Pass |
| 2 | Owner tenants `/tenants` | Pass |
| 3 | Owner signups `/signups` | Pass; empty-state data |
| 4 | Owner plans `/plans` | Pass |
| 5 | Owner subscriptions `/subscriptions` | Pass |
| 6 | Owner invoices `/invoices` | Pass; empty-state data |
| 7 | Owner licenses `/licenses` | Pass |
| 8 | Owner deployments `/deployments` | Pass; empty-state data |
| 9 | Owner flags `/feature-flags` | Pass |
| 10 | Owner audit `/audit` | Pass; internal table scrolling |
| 11 | Owner account `/account` | Pass |
| 12 | Admin dashboard `/admin/` | Pass |
| 13 | Admin accounts `/admin/users` | Pass |
| 14 | Admin hierarchy `/admin/hierarchy` | Pass; expanded state also captured |
| 15 | Admin roles `/admin/roles` | Pass |
| 16 | Admin events `/admin/events` | Pass |
| 17 | Admin betting `/admin/betting` | Pass |
| 18 | Admin points `/admin/points` | Pass |
| 19 | Admin reports `/admin/reports` | Pass |
| 20 | Admin audit `/admin/audit` | Pass; internal table scrolling |
| 21 | Admin settings `/admin/settings` | Pass |
| 22 | Admin subscription `/admin/subscription` | Pass |
| 23 | Admin profile `/admin/profile` | Pass |
| 24 | Declarator `/admin/declarator` | Pass; disabled provider in demo captures |
| 25 | Player arena `/` | Pass; disabled provider in demo captures, playback tested separately |
| 26 | Player events `/events` | Pass |
| 27 | Player history `/history` | Pass; intentional board scrolling |
| 28 | Player predictions `/predictions` | Pass; internal table scrolling |
| 29 | Player results `/results` | Pass |
| 30 | Player points `/points` | Pass |
| 31 | Player notifications `/notifications` | Pass |
| 32 | Player activity `/activity` | Pass |
| 33 | Player profile `/profile` | Pass |
| 34 | Player settings `/settings` | Pass |

Additional numbered screenshots and health checks are indexed by state in
[flow coverage](flow-coverage.md), [flows.json](after/flows.json),
[additional states](after/flows-extras.json) and the gallery: Owner tenant's eight tabs; tenant,
plan, license and deployment dialogs; tenant/plan validation; Admin account/event
dialogs and validation; account/event details; five settings tabs; four betting
tabs; points report; Player event/match details and account menu; each portal's
login and navigation drawer. Registration, password-change pages, the no-access
page, expanded account hierarchy and collapsed desktop navigation were also
captured. All captured dialogs fit the tested viewports.

The detail inspector reports normal input scrolling for long email/endpoint
values, visually hidden file inputs, and switch shadow extents. These are not
page overflow or clipped currency defects.

## Files changed in this continuation

Frontend implementation:

- `web/shared/components/data-table.ts`, `data-table.scss`, `primitives.ts`, `shell.scss`
- `web/shared/services/realtime.service.ts`
- `web/shared/arena/video-stage.ts`, `video-stage.scss`
- `web/shared/styles/_midnight.scss`
- `web/projects/owner/src/app/pages/plans.ts`, `tenants.ts`
- `web/projects/admin/src/app/pages/events.ts`, `event-detail.ts`, `user-form.ts`, `reports.ts`, `betting.ts`, `hierarchy.ts`
- `web/projects/player/src/app/core/arena.store.ts`, `play-betting.ts`
- `web/projects/player/src/app/layout.ts`, `layout.scss`
- `web/projects/player/src/app/pages/events.ts`, `event-detail.ts`
- `web/projects/player/src/app/ui/fight-betting-panel.ts`, `fight-betting-panel.scss`

Verification and documentation:

- `web/shared/components/data-table.spec.ts`, `web/shared/services/realtime.service.spec.ts`
- `web/projects/admin/src/app/pages/reports.spec.ts`, `hierarchy.spec.ts`
- `web/projects/player/src/app/core/arena.store.spec.ts`, `play-betting.spec.ts`
- `web/projects/player/src/app/ui/video-stage.spec.ts`
- `web/e2e/arena-responsive.spec.ts`, `betting.spec.ts`, `declarator.spec.ts`, `owner.spec.ts`, `tenant-flows.spec.ts`
- `web/e2e/global-setup.ts`, `helpers.ts`
- `web/scripts/ui-audit.cjs`, `ui-flows.cjs`, `ui-report.cjs`
- `docs/testing.md`, `docs/ui-ux/`

## Validation

| Check | Result |
| --- | --- |
| `npm run build:all` | Owner, Admin and Player production builds pass |
| `npm run test:unit` plus final Admin rerun | 154 pass: 50 shared/Owner, 39 Admin, 65 Player |
| `.venv/bin/python -m pytest unit_tests -q` (from `api/`) | 71 pass |
| `.venv/bin/python -m pytest tests -q` (from `api/`) | 157 pass against isolated PostgreSQL test database and Redis DB 15 |
| `npm run e2e` | 18 pass against the real local API and seeded test tenant |
| Final focused arena/betting rerun after currency typography adjustment | 3 pass |
| Main route and detail/dialog browser inventories | 306 + 141 measurements; no page-level overflow |
| `git diff --check` | Pass |

The full browser suite initially exposed an exhausted demo event quota and stale
test assumptions about the Branding tab, fresh-tab tenant context, and starting
play balance. Tests now seed a separate local tenant per run, configure the test
grant explicitly, preserve the tenant in new tabs, and choose the Branding tab.
Subscription enforcement and production accounting were unchanged. Test tenants
remain in the local development database for investigation.

## Remaining verification limits

- The interactive demo's external provider is disabled. Licensed demo playback
  and simulated updates were verified; a real provider's authenticated HLS/DASH
  stream still needs verification with its valid configuration and credentials.
- Browser execution used headless Chromium. Physical devices, Safari/Firefox,
  native fullscreen, and a screen-reader listening session were not verified.
- The screenshots show the working tree at the start and end of this continuation,
  not the application before Claude's earlier changes. Detail captures were taken
  as the audit progressed. Live counts and test data can differ between captures.
- Views requiring absent data (for example a populated invoice detail), every
  possible role/permission combination, every deep hierarchy expansion and every
  error-state variant were not visually exercised. The existing integration and
  end-to-end tests cover many of their functional guarantees.
- Screenshots are viewport captures inside application scroll containers; they
  do not assert that every below-the-fold pixel has been manually inspected.
- Unit tests print existing jsdom media-method warnings. Real playback is checked
  by Chromium, rather than inferred from those unit stubs.

This is evidence for the tested states and specific fixes above, not a blanket
claim that no UI defect can remain under arbitrary production data.
