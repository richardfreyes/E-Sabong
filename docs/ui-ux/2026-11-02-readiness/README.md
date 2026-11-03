# Current rendered UI evidence

The separate `READINESS_VISUAL=1` Playwright job passed on 2 November 2026, using the migrated scratch API on 8001 and current Angular portals on 4300–4302. **34 routes × 9 widths = 306 screenshots/measurements**; [inventory](inventory.json), [failures](failures.json). No balances were mocked in this inventory.

Widths: **320, 375, 390, 430, 768, 1024, 1280, 1440, 1920**. All captured pages had zero document/content horizontal overflow and no uncaught page errors. Gameplay-enabled Player pages had exactly one header balance.

1. **Owner:** subscription/reporting/navigation screens. PHP appears in legitimate subscription billing; see [contact sheet](contact-owner.jpg).
2. **Admin:** account management, reports and read-only archive. The archive clearly states that old points are separate and cannot be moved; [archive detail](admin-points-1440.png), [contact sheet](contact-admin.jpg).
3. **Player:** dashboard, events/history, predictions/results, Arena Points ledger, notifications/activity/profile/settings. System-granted balance came from backend records; [phone arena](player-dashboard-390.png), [statement](player-arena-points-390.png), [contact sheet](contact-player.jpg).
4. **Declarator:** assigned event/results controls, real unconfigured-video state, Meron red, Wala blue and Draw green; [phone desk](declarator-declarator-390.png), [contact sheet](contact-declarator.jpg).

The four contact sheets and detailed phone/archive screenshots were visually inspected. Navigation, typography hierarchy, responsive cards, status/disabled controls and empty/archive states remain coherent. No redesign or gameplay color changes were made. Existing regression tests exercise form/dialog validation, retry recovery, double-submit protection, phone menus, revocation and live-update stability.

The first visual fixture left gameplay disabled, so the required header assertion failed. [Original inventory](inventory-disabled.json), [original assertions](failures-disabled.json), [disabled state](player-disabled-390.png) are retained. The fixture was corrected through the real settings API and restores the previous setting in `finally`; assertions were not relaxed.

Limits: dark theme and Chromium only for the broad inventory; no formal contrast matrix, assistive-technology certification, quantified CLS study or exhaustive modal-state screenshots. The unconfigured video warning is real and expected: no feed rights/credentials were supplied. These captures do not prove authorized third-party video distribution or WCAG conformance.

Reproduce from an isolated E2E environment:

```bash
READINESS_VISUAL=1 UI_EVIDENCE_DIR=../docs/ui-ux/<fresh-run> npm run e2e -- readiness-visual.spec.ts
```

Supply the documented `E2E_*` scratch URLs/database environment first; never point fixture setup at production. The normal suite skips this explicit evidence job, which was run separately and passed.
