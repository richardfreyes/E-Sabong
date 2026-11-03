# Neutral tournament engine

The simulated arcade/tactical tournament backend now includes the match controller,
player wager API, arena-scoped WebSocket gateway, and atomic settlement. It uses the
existing FastAPI/JWT stack, tenant identities and **one Arena Points wallet** per player.
Arena Points cannot be purchased, transferred, redeemed or exchanged for cash. Virtual
simulation fees are retained in immutable receipts; no host commission wallets exist.

The domain uses Team A / Team B. Existing gameplay and the read-only legacy points
archive remain separate from this controller; no balances or historical records are merged.

## State controller

The allowed transitions are enforced by both the service and PostgreSQL:

```text
PENDING -> WAGERING_OPEN -> FINAL_CALL -> WAGERING_CLOSED
    -> SIMULATION_RUNNING -> RESULT_DECLARED -> SETTLED
```

The arrow from `WAGERING_CLOSED` continues to `SIMULATION_RUNNING`; the first line
wraps only for readability.

`WAGERING_OPEN` can also close directly. `FINAL_CALL` may reopen on a false start.
Only `WAGERING_OPEN` accepts NEW wagers, following the final requested SQL guard;
final call publishes its countdown but rejects new wagers. Countdown expiry does not
silently change state: the Declarator closes the match explicitly. Draw/cancel outcomes
follow the same controller path; there is no shortcut around the allowed states.

Only a tenant Admin with `simulations.manage`, or an assigned Declarator with
`simulations.control`, can transition a match. Host/player roles cannot gain controller
access merely by presenting an arena ID. Existing role aliases remain `SYSTEM_ADMIN`
(tenant-scoped), `MASTER_HOST`, `REGIONAL_HOST`, `DECLARATOR`, and `PLAYER` without
renaming stored users or changing JWT audiences.

Each transition requires `request_id` and `expected_version`. Illegal/stale transitions
return 409. An identical committed command returns its original response with
`replayed=true`; changing that command's payload or target match returns 409. An already
settled match returns the same settlement receipt even under a new settlement request ID.
Only one non-pending/non-settled match may occupy an arena at a time.

## API

All paths below use `/api/v1/simulations`. JWTs must belong to the tenant selected by the
request host. The existing events/matches module entitlements also apply.

| Method / path | Behavior |
|---|---|
| `GET /wallet` | Caller-only shared Arena Points balance; grants once from configured system allocation when a neutral arena is enabled; existing balances remain readable otherwise |
| `GET /capabilities` | Own role/permissions, states and non-monetary disclaimer; engine available |
| `POST /arenas` | Admin creates an arena, disabled by default |
| `PATCH /arenas/{arena_id}` | Admin explicitly enables/disables admission |
| `GET /arenas`, `GET /arenas/{arena_id}` | Admin sees tenant arenas; Declarator sees assigned arenas; players/hosts see enabled public arenas |
| `PUT/DELETE /arenas/{arena_id}/controllers/{user_id}` | Admin assigns/revokes an active same-tenant Declarator; audited and idempotent |
| `POST /arenas/{arena_id}/matches` | Admin creates a pending neutral match with number, team names and decimal simulation fee |
| `GET /arenas/{arena_id}/matches` | Authorized, paginated match snapshots |
| `GET /matches/{id}` | Current version, state, pools, odds, deadline and settlement receipt ID |
| `POST /matches/{id}/transition` | Authorized controller action, including settlement |
| `POST /matches/{id}/wagers` | Player-only, versioned pool update and atomic Arena Points debit |
| `GET /matches/{id}/settlement` | Authorized aggregate receipt; no participant list |
| `GET /matches/{id}/receipts` | Only the caller's own settlement lines; remains readable after arena disablement |

Example transition:

```json
{"request_id":"<UUID>","expected_version":0,"new_state":"WAGERING_OPEN"}
```

Final call additionally accepts `final_call_seconds` (1–300, default 10). Result
declaration requires `outcome`: `SIDE_A`, `SIDE_B`, `DRAW`, or `CANCELLED`. Persisted and
HTTP snapshot values remain lowercase `team_a`/`team_b` for compatibility; uppercase
state names and side aliases are accepted on requests.

Example wager:

```json
{"request_id":"<UUID>","side":"SIDE_A","amount":"10.00"}
```

Amounts travel as finite decimal strings, with at most two decimal places and a positive
value. JSON numbers, fractional cents and NaN are rejected. Closed-state new requests
return 400 `wagering_closed`. An identical prior wager can still be replayed after closing:
it returns the same stake ID and current wallet without another debit. Payload changes
under an existing key return 409. Insufficient balance returns 422.

Opening requires an enabled arena. `GET /wallet` also supports a neutral-only client while the older gameplay switch is off.
Starting Arena Points reuse the existing audited,
once-per-player system allocation settings; enabling an arena does not invent a balance.
There are no transfer, manual loading, cash-out, redemption or commission endpoints.

## Concurrency and settlement

Each state transition, wager and arena enable/disable uses a fresh `SERIALIZABLE` database transaction. Authentication reads are
released before opening it, preventing queued requests from occupying two pool connections.
A small per-worker mutex reduces contention; it is never the authority for match state.

The lock order is arena -> controller assignment -> match -> affected wallets ordered by
UUID. Conditional pool updates and `INSERT ... SELECT ... WHERE EXISTS` both require an
open match. A PostgreSQL BEFORE INSERT trigger independently checks state and admission.
The deferred foundation guard also requires a matching Arena Points debit in the same
outer transaction and a wallet that reconciles to its ledger. No savepoints wrap postings.

A close takes effect at acquisition/commit of the database state lock. A wager serialized
before it may commit; a wager serialized after it is rejected. Network arrival times across
replicas cannot define a globally ordered millisecond boundary. Serialization/deadlock
failures discard the entire attempt and retry up to eight times. Exhaustion returns 409
`serialization_retry`; retry the unchanged payload and request ID.

The final requested payout formula is used:

```text
total = Side A pool + Side B pool
fee = round_half_up(total * simulation_fee_rate, 2 decimal places)
net = total - fee
winner gross return = net * (winner stake / winning pool)
```

Shares are allocated in integer cents with largest remainders, ties in stable placement
order. Consequently `sum(credits) + fee == total` exactly. With 10 Arena Points on each
side and a 5% fee, the winning side receives 19 Arena Points in total. This follows the
final net-pool formula, rather than the earlier alternative that charges only the losing pool.

DRAW and CANCELLED refund every initial stake with zero fee. A decisive outcome with an
empty side pool also refunds all stakes: no opposing pool is manufactured. Empty matches
settle safely. Repeated settlement calls never duplicate credits or refunds.

`simulation_settlements` holds one immutable receipt per match, with aggregate totals and
private per-stake lines (`SETTLEMENT_WIN`, `SETTLEMENT_REFUND`, `SETTLEMENT_LOSS`). Positive
credits/refunds link to the existing append-only wallet ledger. Receipt creation, state,
wallet postings, command receipt and audit event commit together. Failure after some
postings rolls back all wallets, pools and receipts. Shared-wallet reporting includes active
neutral stakes and virtual fees in reconciliation.

## WebSocket protocol

Use the existing native WebSocket transport, not a Socket.IO packet protocol:

```text
/api/ws/simulations?token=<tenant access token>
```

An Authorization Bearer header is also supported for native clients. Local development
may add `tenant=<slug>` when the existing development override is enabled. JWT/tenant,
module entitlement, account status, expiry and session revocation are checked. No token
is recorded by the gateway's application code.

The server sends `CONNECTED`. Subscribe using:

```json
{"type":"SUBSCRIBE","arenaId":"<arena UUID>"}
```

The server derives `tenant:{tenant_id}:arena:{arena_id}`. A supplied foreign `tenantId` or
mismatched room is rejected. Maximum eight rooms per connection. `SUBSCRIBED` is sent
only after Redis acknowledges the subscription and includes authorized match snapshots.
`UNSUBSCRIBE` leaves a room. `PING` receives `PONG`. Invalid subscriptions receive `ERROR`;
authentication or lost room access closes with 4401/4403.

Broadcast frames use `{type, room, data}`:

| Event | Data |
|---|---|
| `MATCH_STATE_CHANGED` | `matchId`, `previousState`, `newState`, `version`, UTC `timestamp`, optional final-call deadline |
| `ODDS_UPDATED` | `matchId`, `version`, decimal `totalSideA`, `totalSideB`, `oddsSideA`, `oddsSideB` (null until both pools have stakes) |
| `COUNTDOWN_TICK` | `matchId`, `version`, `remainingSeconds`, authoritative `endsAt` and UTC `timestamp` |

State and odds are published through Redis only after commit, reaching subscribed sockets
on every API replica. Rooms expose aggregate pools only; private balances continue using
the existing per-user `wallet.updated` channel. Countdown pulses are computed from the
persisted server deadline, including for clients that join during final call.

Clients must discard lower match versions, cancel countdowns when a newer state arrives,
and refetch `GET /matches/{id}` on reconnect. Redis pub/sub is ephemeral: there is no
claim of durable exactly-once event delivery during a process crash or Redis outage. The
immutable database receipt remains authoritative. Local tests bound normal state-event
latency below one second; production network latency is not guaranteed by that test.

## Migrations and rollout

Migration 0014 remains the identity-preserving foundation. Migration
[0015](../../db/migrations/versions/20261102_0015_simulation_engine.py) adds settlement
receipts, the active-match unique index, row-state/stake guards and the deferred receipt
check. It does not convert or move old balances. PostgreSQL requires UPDATE privilege for
row-locking reads; only `simulation_controllers.created_at` is additionally granted for
assignment locking, and no assignment-edit API is exposed.

[Reviewable offline SQL](migration-0015.sql):

```bash
cd api
.venv/bin/alembic upgrade 0014:0015 --sql > /secure/path/simulation-0015.sql
```

Before approval to migrate production: record the current revision, freeze writes, verify
a fresh backup by restoring it, and rehearse on an isolated copy. At 0014, check for more
than one active match per tenant/arena before creating the unique index. Existing users,
legacy point rows, wallet balances and audit trails must be preserved. Reopen only after
API, worker and client compatibility checks. Arenas require explicit enablement.

Prefer application rollback while retaining 0015. Its downgrade refuses to erase stored
settlement receipts. Do not downgrade an environment with active simulation activity;
retain the schema and receipts while repairing/redeploying. Foundation 0014 also refuses
to discard nonempty simulation history. Never truncate production history to force a
rollback. Test cleanup truncation is restricted to the disposable `esabong_test` database.

[Verification results](verification.md) distinguish disposable DB, scratch restore and
browser evidence. No production migration, commit, push or deployment was performed.
