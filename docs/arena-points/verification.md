# Verification — 2026-11-02

All commands completed with exit code 0. Integration tests used disposable
`esabong_test` with local PostgreSQL and Redis. Browser tests used the migrated scratch
restore, separate API/frontend ports and a separate Redis database.

| Check | Result |
|---|---|
| `cd api && .venv/bin/python -m pytest tests -q` | 165 passed |
| `cd api && .venv/bin/python -m pytest unit_tests -q` | 98 passed |
| `cd api && .venv/bin/ruff check app tests unit_tests` | Passed |
| `cd web && npm run test:unit` | Owner/shared 57, admin 58, player 75: 190 passed |
| `cd web && npm run e2e` (isolated environment) | 27 passed |
| `cd web && npm run e2e -- points-management.spec.ts` | 2 additional responsive confirmations passed |
| `cd web && npm run build:all` | Owner, admin, player production builds passed |
| `bash -n db/scripts/verify-backup.sh` | Passed |
| `git diff --check` | Passed |
| Source archive verification | Source remains at 0012; all archived row fingerprints unchanged |
| Restore and migration rehearsal | Full-content restore verified; 0012 → 0013 → 0012 → 0013; archive fingerprints identical |

The complete [browser results](e2e-results.txt) cover live cross-player balance/pool
updates, results and settlement, authorization, archive write refusal, one header
balance matching gameplay and history, nine responsive widths, red/blue/green panel
states, and actual video playback. Extreme-value read interceptions are restricted to
test code; application balances and gameplay processing use real database records.

[Archive integration tests](../../api/tests/test_points_archive.py) preserve every
stored field across migration, including original audit records and gameplay ledgers.
They also cover archived filters, pagination, CSV scope, downline and tenant isolation,
forbidden writes at API/database levels and caller-only Arena Points history.

Backend integration and offline unit tests run as separate commands: combining them
would inherit the integration environment and invalidate the development-defaults unit
fixture. Angular's jsdom media-method notices are test-environment messages; the real
browser playback checks passed.

Reviewed screenshots from the real scratch API (system allocations, no intercepted
balance values): [Player 320px](screenshots/player-320.png),
[768px](screenshots/player-768.png), [1440px](screenshots/player-1440.png);
[Archive 320px](screenshots/archive-320.png), [768px](screenshots/archive-768.png),
[1440px](screenshots/archive-1440.png). New tenants correctly have no legacy accounts.

No commit, push, deployment or production migration was performed. The local source
legacy archive remains unchanged; only the test database and scratch restore were migrated.
