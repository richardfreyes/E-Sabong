# Proposed commits — review only

No `git add`, commit, reset or push was performed. Preserve the user's existing staged deletions/renames. Review each hunk and attach matching tests before committing. These are logical review groups; the release is the complete compatible set, not seven independently deployable artifacts. Migrations must precede API traffic when the complete release is deployed.

| Group | Proposed title | Paths / scope |
|---|---|---|
| 1. Arena Points | `refactor(points): retire transfers and expose one Arena Points balance` | `api/app/modules/demo_points/`, user/tenant/portal legacy-account removal, caller-only betting entries, shared Arena Points pipe/model/service contracts, Player wallet/history/route changes |
| 2. Frontend/UI | `fix(ui): preserve responsive arena controls and neutral amount labels` | Player gameplay panel SCSS/TS, header/layout/dashboard, Admin/Declarator amount labels, archive/account-picker UI, obsolete amount-picker removal |
| 3. Backend/API | `feat(simulations): enforce tenant-scoped state and atomic virtual settlement` | `api/app/modules/simulations/`, permissions, router/model registration, reconciliation/report changes; Redis pool isolation, configuration and both WebSocket gateways |
| 4. Database/integrity | `feat(db): preserve points archive and guard simulation receipts` | **new** 0013/0014/0015 migrations only; archive report CLI; `db/scripts/backup.sh`, `verify-backup.sh` safety and preservation checks. 0012 remains untouched |
| 5. Infrastructure | `fix(ops): isolate staging and make preflight non-destructive` | Compose Redis settings, new staging overlay/example, env/build-context ignores, corrected preflight and deployment safety |
| 6. Automated tests | `test: verify archive, shared balances, tenant isolation and staging safety` | Backend tests/unit tests including removed obsolete transfer tests and replacement archive coverage; frontend pipe/component tests; E2E workflows and opt-in nine-width inventory |
| 7. Documentation | `docs: record current readiness evidence and guarded deployment plan` | Current readiness/deployment/arena-points/database/API/OpenAPI docs, simulation runbooks, UI findings/screenshots and redacted regression/restore/security evidence |
| Separate performance commit | `perf(simulations): add synthetic concurrency benchmark and measured failures` | `scripts/benchmarks/`, `docs/benchmarks/`; keep large per-player JSONL separate from core application changes. The failed peak-load report is retained |

Review dependency boundaries before staging: shared pipe export and imports must land together; model registration requires its schema; behavior changes require their regression tests. It is acceptable to move a test into its owning feature commit rather than create a temporarily broken commit. Do not mechanically split a file across groups without reviewing dependencies.

`git-review.json` inventories working files against the existing HEAD. PNG and JSONL evidence are intentionally large (roughly 26 MiB new visual evidence and 19 MiB benchmark evidence); decide during review whether release-source history or a controlled artifact store should hold them. Do not delete them automatically. Generated `web/dist`, virtualenvs, caches, private fixture/JWT/Compose files and database dumps are excluded.
