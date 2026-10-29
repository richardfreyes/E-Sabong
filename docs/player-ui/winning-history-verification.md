# Winning history board

Implemented in the existing Angular Player Portal only. No backend, Admin or Owner changes.

## Behavior

- `WinningHistoryBoardComponent` is reused by the dashboard, event detail and match history views.
- `toWinningHistoryGrid` is a pure, non-mutating TypeScript utility. Results are sorted by fight number, validated and deduplicated; the highest official revision is retained.
- Consecutive Meron/Wala wins descend in one column. A winner change starts the next column at row zero.
- Six rows are used. Longer streaks wrap to the next column, retaining their streak ID and showing a dashed continuation connector.
- Draw and Cancelled each occupy a neutral column and end the winning streak. No contest remains gray and keeps its official label. Missing fight numbers break continuity; no missing outcomes are inferred.
- Only finalized official results/cancellations appear. Live and upcoming fights remain on the separate fight card.
- Result circles retain their match-detail navigation, correction marker and accessible outcome labels.
- Existing WebSocket messages update the store and board immediately. Duplicate/older revisions are ignored. Late API snapshots cannot overwrite a newer revision or drop a newly received result awaiting reconciliation.
- New/corrected results animate once; repeated messages do not restart the animation. Horizontal scrolling follows the latest record, including after resizing. Reduced-motion preferences are respected.
- The obsolete configurable row-count control was removed so the board always respects the six-row limit.

## Verification

- Production build passed:
  `CI=1 NG_BUILD_SASS_EMBEDDED=0 npm --prefix web run build -- player --configuration production --progress=false`
- All 59 frontend unit tests passed:
  `CI=1 NG_BUILD_SASS_EMBEDDED=0 npm --prefix web run test:unit`
  (21 shared/Owner-target tests, 3 Admin-target tests, 35 Player tests.)
- New coverage includes four consecutive wins for each side, alternating sides, the requested seven-fight example, streaks longer than six, neutral outcomes, duplicate results, official corrections, missing/invalid fight numbers, empty history, immutability, cell uniqueness, detail selection, animation deduplication and automatic scrolling.
- Integration-style Angular test feeds representative frames through the existing ArenaStore realtime subscription into a mounted board, verifying reactive rendering without recreating the component, preserved earlier DOM nodes, duplicate suppression and correction reflow.
- Browser checked against the existing Champions Derby card: 74 finalized records from 80 fights, with 74 unique circles. No historical records were created or changed. Fights 1–4 occupy column 0, rows 0–3; fight 5 begins Wala in column 1; fight 6 is a green neutral column; fight 7 begins another Wala column. Fights 25–32 demonstrate six-row overflow, with fight 31 starting the linked continuation.
- Desktop measured six 32px grid rows. Automatic scroll position reached its 563px maximum.
- At 390 × 844, the board retained six 29.59px rows, scrolled to the latest results, and caused no document horizontal overflow.
- Circle navigation opened fight 74's existing official Draw detail. The shared event-page board was also checked.
- Final browser warning/error log was empty.
- Live server delivery was not stimulated by declaring or changing an official match result. WebSocket update behavior was verified with injected test frames; browser inspection used existing API history and the live connection. Full end-to-end suite not rerun.

## Visual evidence

- `winning-history-reference.png`: supplied grid reference.
- `winning-history-comparison.jpg`: supplied reference alongside the rendered board.
- `winning-history-event.jpg`: desktop event page showing the first four-win Meron streak and overflow connector.
- `winning-history-board.jpg`: crop of that event's statistics and streak board.
- `winning-history-desktop.jpg`: dashboard with the latest results visible.
- `winning-history-mobile.jpg`: 390px phone view with latest results and statistics.

The browser preview was returned to normal desktop sizing and left on the existing event page for inspection.
