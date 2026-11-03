# Production readiness — current review, 2 November 2026

| Gate | Classification |
|---|---|
| Technical staging readiness | **Conditionally ready** |
| Public production launch readiness | **Not ready** |

Current code passed **194 backend integration + 108 backend unit tests, 190 frontend tests,
28 E2E tests**, a separate **306-capture visual job**, all three portal production builds
and the API/Caddy Docker builds. The ordinary E2E run also skipped the explicit visual job,
which was separately enabled and passed. These are new results, not historical baselines.

The complete [deployment-readiness report](readiness/2026-11-02/REPORT.md) contains findings,
files/evidence, database and backup verification, security limits, legal/operational blockers,
deployment sequence and rollback plan. The [proposed Git plan](readiness/2026-11-02/COMMIT_PLAN.md)
is for approval; existing staged changes were preserved and nothing was committed or pushed.

The local source remains at **0012**, with **0013–0015 pending**. Scratch upgrade/rollback
rehearsals preserved historical points and Arena Points hashes. A quiescent scratch backup
restored exactly; a source-to-restore comparison correctly reported a live usage-counter
mismatch. Production backup/recovery still requires an approved quiescent target drill.

One player-facing Arena Points balance, read-only legacy history, red Meron / blue Wala /
green Draw and PHP-only subscription billing are preserved in the candidate. There are no
Arena Points purchase, cash funding, transfer, redemption or cash-out operations.

Confirmed Redis command/subscription pool contention, destructive preflight teardown,
scratch overwrite and env-file exclusion issues were fixed and regression checked. The
prior 500-request/1,000-socket benchmark failed; focused pool tests do not replace a full
separate-machine staging performance rerun.

Candidate images and a separate staging configuration are prepared, but **no candidate
staging deployment was started**, following the user's instruction not to deploy without
approval. New-container TLS, health/restarts, worker execution, external video rights,
real alert delivery, off-site recovery and production host/network checks remain pending.
Public operation also needs legal, privacy and operational sign-off.

The superseded readiness text is [archived](readiness/2026-11-02/previous-production-readiness.txt).
It is historical evidence and must not be used as current approval.
