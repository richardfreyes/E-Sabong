# Player Portal refinement — visual QA

final result: passed

## Scope and visual truth

- Source: `docs/player-ui/reference.png` (580 × 723 pixels). This is the supplied control-panel layout reference, not a target for a pixel-identical rebuild.
- User-directed adaptations: a 60/40 video-led desktop layout; fight information before video on phones; only MERON and WALA in the panels; no entry names, practice badges, payouts, wagering, draw wagering, or monetary amounts.
- Implementation: existing authenticated Player Portal at `http://localhost:4202/`, tenant Demo Arena, live demo events. Admin, Owner, shared authentication, and FastAPI implementations were not changed.
- Full-view comparison: `docs/player-ui/comparison.jpg`, containing the reference, desktop implementation, mobile main controls, and mobile history in one comparison image. Screenshots retain original dimensions; the contact sheet scales each proportionally for comparison, without claiming equal viewport fidelity.

## Browser evidence

- `docs/player-ui/desktop.jpg`: 1291 × 904, sparse four-fight event.
- `docs/player-ui/desktop-dense.jpg`: 1291 × 904, Champions Derby, current fight 75, 80 total fights.
- `docs/player-ui/mobile.jpg`: 390 × 844, same 80-fight event and selected MERON state.
- `docs/player-ui/mobile-records.jpg`: 390 × 844, scrolled to results and statistics.
- `docs/player-ui/mobile-narrow.jpg`: 320 × 740, after correcting compressed branding.
- `docs/player-ui/match-detail.jpg`: existing match route with plain MERON/WALA and official result.
- Browser screenshots use device scale factor 1. Their pixel dimensions equal the CSS capture dimensions. The supplied reference has no known device scale; it is treated as a layout reference and compared proportionally.
- Focused comparisons: inspected the supplied panel labels, amount controls, result circles, and statistics against the corresponding implementation regions at full image resolution; inspected mobile controls and the separate mobile history capture. No reproduction of monetary fields or decorative chip imagery was intended.

## Findings and corrections

1. [P2, fixed] Sparse records wasted vertical space by placing four fights down an otherwise empty six-row board. Small cards now use one row; populated cards preserve the configured grid height. Post-fix evidence: `desktop.jpg` and `desktop-dense.jpg`.
2. [P2, fixed] At 320px the tenant mark compressed while the application name truncated to almost nothing. The mark now retains its width and the redundant name hides at the smallest breakpoint. Post-fix evidence: `mobile-narrow.jpg`; measured mark width 33.59px, document width and scroll width both 320px.
3. [P2, fixed] The old selection layout pushed amount controls below a tall named-entry panel. The compact control surface now contains fight header, equally sized plain side panels, numeric amount and four quick controls. Main video and controls are visible on desktop; mobile shows the complete primary interaction above records. Evidence: desktop and mobile captures.

No actionable P0/P1/P2 visual findings remain for the requested refinement.

## Required fidelity surfaces

- Typography: existing Archivo interface text and Big Shoulders Display headings retained; condensed uppercase MERON/WALA and large fight number match the visual direction. Labels remain readable without clipped text.
- Spacing/layout: desktop video measured 619 × 348.19px (16:9), beside a 414px control column, approximately 60/40. Phone order measured as fight header y=80, video y=181, sides y=448, amount y=549, records y=754 at 390px. No document horizontal overflow at desktop, 390px, or 320px. The history grid scrolls horizontally within its own region.
- Colors/tokens: dark graphite surfaces with tenant-controlled Material accents preserved. MERON red, WALA blue, draw green and cancelled/no-contest gray remain consistent across labels, result circles and statistics. Selected sides use a filled surface plus a check indicator and native checked state.
- Images/assets: existing licensed Big Buck Bunny sample video retained; only the bundled demo source is accepted. Dynamic tenant logo/name behavior retained. Existing Material icons used. No fabricated broadcast imagery or new image assets.
- Copy/content: no rendered custom competitor names, entry descriptions, practice titles, practice descriptions or practice badges in the inspected dashboard, history labels, and match-detail view. Amounts explicitly use demo points; non-redeemability is stated compactly. No payouts, wagering, settlement or cash-out controls.

## Functional verification

- Browser: both side choices, checked state, quick amount selection, numeric over-balance error, recovery via a quick amount, pause/play, entering/exiting fullscreen, event switching, dense result board, result-to-detail navigation, desktop/mobile layouts and fixed mobile navigation.
- The balance stayed at 1,000 demo points during side and amount changes.
- Final browser console check: no warnings or errors captured.
- Live connection indicator observed on the existing authenticated FastAPI/WebSocket integration. Automatic advancement and corrected statistics tested with the real ArenaStore consuming representative realtime messages; no new official result was declared against the sample live card during this browser session.
- Unit tests: 38 passed (21 shared/Owner-target tests, 3 Admin-target tests, 14 Player tests). Includes 9 new checks for numeric validation/recovery, realtime advancement/correction/event filtering, demo-only media source, buffering, media errors and offline recovery.
- Production Player build passed, with all production optimizations except font inlining. Player-only font inlining is disabled so the build does not require reaching Google Fonts; existing runtime font links remain. No Admin or Owner build configuration changed.
- Reproducible commands from `web/`:
  - `CI=1 NG_BUILD_SASS_EMBEDDED=0 npm run build -- player --configuration production --progress=false`
  - `CI=1 NG_BUILD_SASS_EMBEDDED=0 npm run test:unit`
- CI mode avoids the local native-cache abort observed in this sandbox; the JS Sass compiler avoids depending on the embedded compiler during verification.
- Updated the existing E2E player's match-link locator to its new fight-number label. The full end-to-end suite was not rerun: shell access to local network services is blocked. This report does not claim newly exercised end-to-end result delivery or production deployment.

## Implementation checklist

- [x] Retain existing Angular app, APIs, authentication, tenant branding and realtime integration.
- [x] Remove custom entry names and all practice-selection UI.
- [x] Keep a large responsive demonstration video and prominent fight number.
- [x] Keep plain, equal red/blue side panels and compact demo amount controls.
- [x] Preserve results, statistics, and individual match routes.
- [x] Check desktop, phone and narrow-phone layouts; fix substantive issues.
- [x] Run production build and frontend unit suites.
- [x] Save screenshots and compare with the supplied reference.

## Follow-up polish and limits

- Populated cards place some statistics below the initial desktop viewport; they remain directly below the result grid and accessible with normal scrolling. Primary video and controls remain above the fold.
- Browser validation used Chrome; separate Safari and Firefox checks were not performed.
- No backend changes, deployments, or live result mutations were made.
