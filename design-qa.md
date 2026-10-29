# Midnight Arena — full-platform visual QA

Final result: implementation, frontend unit tests, and production builds passed. Browser verification completed for the listed screens and sizes, with the limitations below.

## Scope

- Enhanced the existing Angular Player, Admin, and Owner applications and their shared UI library.
- Preserved FastAPI contracts, authentication flows, role guards, tenant APIs, demo points accounting, subscriptions/licenses, WebSocket services, and recorded outcomes. No backend business code changed for this redesign.
- Target: the user-specified Midnight Arena palette, Inter/Barlow typography, and distinct Player/operations/SaaS layouts. The supplied history reference remains behavioral guidance for the Player grid; monetary fields and prohibited broadcast functionality are excluded.
- Shared design-system guide and screenshot gallery: [docs/midnight-arena/README.md](docs/midnight-arena/README.md).
- Earlier Player-only QA is archived at [docs/player-ui/design-qa-previous.md](docs/player-ui/design-qa-previous.md).

## Visual evidence

The [contact sheet](docs/midnight-arena/platform-overview.jpg) shows all three portals at desktop, tablet, and mobile sizes. Original screenshots are retained at their capture dimensions; contact-sheet images are proportionally scaled.

| Portal | Desktop 1291 × 848 | Tablet 834 × 1112 | Phone 390 × 844 |
|---|---|---|---|
| Player | Video-led arena, fight 75, 74 official records | Stacked arena and controls | Fight → video → sides → amount → records |
| Admin | Operational overview and account table | Existing event and match management | Overview, event forms/cards, reports, settings |
| Owner | Executive overview, tenant table, provisioning dialog | Two-column KPI overview | Overview, drawer navigation, compact tenant table and filters |

Additional captures cover account/provisioning dialogs, Player history/statistics and result details. No page-level horizontal overflow was measured at the inspected desktop, tablet, or phone views. Desktop Player video measured 610 × 343.125 CSS pixels, exactly 16:9. The records board retains its own horizontal scroll region.

## Findings addressed

1. Shared surfaces, typography, controls, status chips, and shell navigation now use one token system instead of independent palettes. No hardcoded hex colors remain in application/shared component TS or SCSS outside tests; palette definitions and runtime defaults are centralized.
2. Admin and Owner previously lacked a persistent compact desktop rail. The shared shell now provides accessible collapse/expand actions and an independent mobile drawer.
3. Tables now have consistent spacing, scroll containment, record counts, skeletons, a debounced search-clear action, and mobile filter disclosure. Existing sorting/pagination and per-page filters remain connected to their original services.
4. Charts use actual returned counts. Owner status charts do not fabricate revenue trends or system health. Single-category charts show one centered category label, and tooltips tolerate categories disappearing during refresh.
5. A phone capture exposed a barely visible, truncated Player brand name. Phones now show the tenant mark/logo while preserving the complete accessible home label and drawer identity. Demo points and account controls have 44px minimum height, as do amount quick controls.
6. Tenant palette generation now preserves neutral Midnight dark surfaces, fixes outcome colors, and uses contrast-mapped action colors. Unsafe bright dark-surface overrides fall back without altering stored branding.
7. Dialogs, menus, tooltips, snackbars, form boundaries, focus rings, busy indicators, and reduced-motion behavior share the same foundations.

No unresolved visual blocker was found in the inspected routes. This is a scoped verification, not a claim that every record/action/browser combination was exercised.

## Functional browser checks

- Owner: authenticated overview and actual metrics; tenant table; new-tenant form opened and cancelled; desktop navigation collapse/expand; invoice empty state; licenses route; tablet/mobile overview; mobile drawer and tenant filter disclosure.
- Admin: sign-in; operational overview; account search and clear; new-account form opened and cancelled; hierarchy expansion; events table and existing Champions Derby management; mobile reports and branding settings.
- Runtime branding: previewed a bright green primary accent through the existing form, observed immediate Material token updates, unchanged Meron red, and Midnight background; discarded the preview without saving.
- Player: authenticated player1; event switch to Champions Derby; MERON/WALA selection; quick amount controls; over-balance numeric validation and recovery; pause/play and refresh; 74 historical outcomes, including draw/cancelled/no-contest and corrected/continued streak labels; record-to-detail navigation; connected live indicator.
- Player balance remained 1,000 demo points during selection and amount checks. No selections submitted transactions or settlement requests.
- No console warnings or errors were captured in the inspected browser sessions. A fullscreen request was denied by the browser automation environment and displayed the existing recoverable error; successful fullscreen entry was not re-established in this pass.
- No new official results, tenants, users, points transfers, invoices, licenses, or brand settings were committed during these UI checks.

## Automated verification

Final run: 28 October 2026, approximately 20:23 Asia/Manila.

- **74 frontend unit tests passed:** 36 shared/Owner-target, 3 Admin-target, 35 Player-target; 16 test files.
- Existing authentication, API-service, registration, Player picks, media recovery, result-grid, duplicate-event, correction, realtime-update, and history reconciliation tests passed.
- Added coverage for default dark mode, safe surface overrides, extreme tenant-accent contrast, fixed outcome semantics, runtime identity/favicon reset, responsive shell navigation, and table search/filter behavior.
- **All three production builds passed:** Owner, Admin, Player. No build warnings/errors were emitted in the saved final output.
- Logs: [frontend-tests.log](docs/midnight-arena/frontend-tests.log), [production-builds.log](docs/midnight-arena/production-builds.log).

Reproduce from the repository root:

```sh
CI=1 NG_BUILD_SASS_EMBEDDED=0 npm --prefix web run test:unit
CI=1 NG_BUILD_SASS_EMBEDDED=0 npm --prefix web run build:all
```

CI mode and the JS Sass compiler avoid the local native-cache/compiler issue. Remote font inlining is disabled for all three production targets; optimization remains enabled and runtime font links/fallbacks are retained.

## Accessibility evidence

- Fixed default palette checks pass normal-text AA: primary/surface 16.85:1, secondary/elevated 6.27:1, gold action 11.37:1, white/Meron 4.83:1, white/Wala 5.17:1. Input boundaries measure 3.80:1 against the surface. Full measured pairs are in [contrast.json](docs/midnight-arena/contrast.json).
- Unit tests verify primary action contrast at least 4.5:1 for six widely different tenant inputs in both light/dark modes.
- Visible keyboard focus, accessible navigation names when collapsed, native checked/pressed selection states, labeled scroll regions, loading/error announcements, chart data tables, and reduced-motion overrides are retained or improved.
- These checks cover the shared design system and representative screens. A complete assistive-technology audit and every possible uploaded image or tenant configuration were not tested.

## Verification limits

- Browser checks used Chrome. Safari/Firefox were not independently exercised.
- The full Playwright end-to-end suite was not rerun because shell access to local network services is restricted; browser checks used the connected Chrome surface. Unit tests and production compilation are recorded separately from browser evidence.
- WebSocket connectivity was observed; realtime advancement, replay deduplication, corrections, and late-history reconciliation were verified through the existing store tests. No fresh official result was declared on the live sample card during this pass.
- Existing optional modules and permissions remain as implemented. No new monitoring backend, billing feature, tenant theme-variation API, or restricted operation was introduced.
- No deployment was performed. UI quality does not establish regulatory authorization for any live operation.

## Subsequent Declarator feature

The Declarator workspace is a separate enhancement. Its implementation and current verification limits are documented in [docs/declarator/verification.md](docs/declarator/verification.md); the earlier Midnight Arena screenshots above do not establish that the new Declarator dashboard has completed live database/browser verification.
