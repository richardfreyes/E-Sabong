# Declarator contract

The dedicated workspace is `/admin/declarator` in the existing Admin Angular app. No fourth application/server is required.

- `results.submit`: Declarator accounts may read only assigned, published events, their match cards, records and declaration audit trails. Submission requires an active assignment, live event, in-progress fight, expected official revision, and confirmation in the UI.
- `results.approve`: authorized reviewers read tenant-wide records, mark a proposal under review, finalize, or return it with a reason. A user cannot approve their own proposal. Built-in tenant administrators receive this permission; operators retain their existing permissions.
- `events.manage`: assign/revoke active Declarator accounts on an event using the existing account management flow. Assignments are audited.

API prefix `/api/v1/declarator`; events and matches modules must be enabled:

| Method/path | Contract |
|---|---|
| GET `/events` | Available assigned events (all tenant events for approvers) |
| GET `/events/{id}/monitor` | Event, complete official card, current active fight (holding the last finalized fight until another starts, or the first scheduled fight on a new card) and its proposal status |
| GET `/records` | Paginated records: event_id, q (exact fight number), winner (official only), workflow status, date_from/date_to (UTC activity date) |
| GET `/matches/{id}/history` | Official revisions, declarations and append-only declaration audit trail |
| POST `/matches/{id}/declarations` | `{request_id: UUID, outcome, base_revision: 0, reason?}`; exact retries return the original proposal; conflicting key/payload or active proposal returns 409 |
| POST `/declarations/{id}/review` | `{reason}`; retain proposal under review |
| POST `/declarations/{id}/approve` | Finalize exactly once after rechecking assignment, event, match state, official base revision, reviewer permission and different actor |
| POST `/declarations/{id}/return` | `{reason}`; cancel proposal, leave official result unchanged; permits a fresh submission |
| GET `/events/{id}/assignments` | Event-manager-only assignment list |
| PUT/DELETE `/events/{id}/assignments/{user_id}` | Event-manager-only assignment management |

Outcomes: meron, wala, draw, no_contest, cancelled. Cancelled requires a 3–300 character reason and finalizes an in-progress fight as cancelled; it cannot amend an already finalized fight. Other corrections require `base_revision > 0`, a reason, and approval; old official results remain visible until approval. Direct legacy result/correction endpoints refuse assigned or workflow-managed matches, preventing bypass.

State: a fight without a proposal is pending. Submission audits declared → pending_approval in one transaction. Review moves to under_review; approval to finalized; return to cancelled. A finalized cancellation has workflow finalized, match cancelled, and official winner cancelled.

New tables use forced PostgreSQL RLS, same-tenant foreign keys, an active-proposal partial unique index, and request-key uniqueness. Declaration audit is append-only for application roles. Match row locks serialize finalization; official results retain the existing unique match constraint and append-only revision history.

Pending/review details use per-user WebSocket channels, addressed only to active assigned Declarators and currently authorized approvers. Only approved outcomes publish existing `match.result_declared`, `match.result_corrected`, or cancellation messages to the tenant channel. Existing Player/Admin stores consume these messages; Player's local non-monetary predictions derive outcomes from those official records without moving points. Reconnects refetch canonical API state; notification ordering never overwrites a newer official revision.

No video-source setting is added: both portals reuse the bundled authorized demonstration clip.

`GET /eligible-users` lists active Declarator account references for event managers.

Verification mode is persistent on the event once an assignment is made. Assignment revocation and event-update payloads cannot disable it. Legacy result actions remain blocked; cancellation of the entire verified event additionally requires `results.approve`. Scheduled-match operational cancellation remains available through its original permissions.
