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
| POST `/matches/{id}/betting/open` | Assigned Declarator only. Opens (or reopens) simulated betting on the event's current fight; opening the next scheduled fight starts it. Returns the `BettingControl` below. Idempotent |
| POST `/matches/{id}/betting/close` | Assigned Declarator only. Stops new simulated bets; pools, odds, bets and balances stay. Returns `BettingControl`. Idempotent |
| POST `/events/{id}/fights` | Assigned Declarator only. `{meron_name, wala_name, meron_details?, wala_details?}`: prepares the next sequential fight with the announced entries, only when the live card has no fight in progress or scheduled (`fight_already_prepared`). 201 `MatchOut` |
| PATCH `/matches/{id}` | Assigned Declarator only. Enters or corrects the entries of the prepared next fight while it is scheduled and betting never opened on it (`not_current_fight`, `match_locked`) |
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

## Betting control (Open / Close Betting)

The monitor includes `betting` (`BettingControl`): `match_id`/`match_number`/`match_status` of the fight the switch acts on
(the fight in progress, otherwise the lowest scheduled fight), `status` (`open` only while the backend accepts bets),
`book_state`, `can_open`, `can_close`, `blocked_reason` and `next_match_number`. The Declarator dashboard renders only this
server state.

- State is the fight's book (`fight_books.state`), already enforced by `POST /portal/matches/{id}/bets`: a closed book returns
  409 `betting_closed`, so stale browser sessions cannot bet. No migration is involved.
- Open requires: Declarator account type, `results.submit`, an active assignment to the event (tenant-scoped; other tenants see 404),
  betting enabled in settings, a live event, the fight being the current one (`not_current_fight`), no proposal awaiting approval
  (`declaration_pending`), no official result (`result_finalized` — a finalized fight never reopens), and no other fight of the
  event with an open book (`betting_open_elsewhere`).
- **One open fight per event, on every path.** `betting.service.open_betting` (used by the Declarator endpoints and by
  `POST /admin/matches/{id}/betting/open`) refuses to open a second fight of an event with 409 `betting_open_elsewhere`
  (`details.match_number` names the open fight). The check runs after the event row is locked `FOR UPDATE`, so concurrent
  opens of different fights serialise and exactly one succeeds. Emergency override (admin path only): an event manager
  (`matches.manage` + `events.manage`) sends `{"supersede": true, "reason": "..."}` (reason ≥ 3 characters); the other open
  fight is closed first (`betting.closed` with `by: superseded`, plus `betting.superseded` with the reason) and the invariant
  still holds.
- Event, then fight, are locked `FOR UPDATE` (the same order as declarations and results), so concurrent or repeated clicks
  serialise; repeats change nothing and write no audit entry.
- Every real change is audited (`betting.opened` with `by: declarator` and `reopened`, `betting.closed` with the frozen pools and
  final odds; `match.started` when opening starts the fight), with actor, IP and timestamp.
- Broadcasts: `book.updated` (tenant-wide aggregate: state, accepting, pools, odds, version), `betting.closed`, and
  `match.updated` when the fight starts. Player, Admin and Declarator stores already consume these.
- Closing or reopening never changes the fight number. Submitting a declaration still closes betting automatically.
- Automatic progression: when an approver finalizes a first (non-amendment) result or cancellation on a live event and no fight
  is scheduled, the next number is created with the side labels `Meron` / `Wala` as placeholders (no participant is invented)
  and audited as `match.next_prepared`. Betting on it stays **closed** until the Declarator opens it. The Declarator enters
  the announced entries with `PATCH /matches/{id}` before opening. Amendments never advance the card.
- The Admin Events page has no "Add match" control; routine fight progression is Declarator-driven. The admin match API is
  unchanged (integrations, emergency use) and historical fights, results and audit trails are untouched.
- Completing an event soft-deletes a fight prepared by the progression (`match.next_prepared`) that is still scheduled and never
  had a book, audited as `match.prepared_discarded`. Any other scheduled or in-progress fight still blocks completion.
- Balances and stakes remain simulated, non-monetary Arena Points with no cash value; settlement logic is unchanged.
