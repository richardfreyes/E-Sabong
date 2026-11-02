# Admin points management

The Admin portal's **Points wallet** page (`/points`) manages the tenant's points ledger.
It reuses `point_accounts`, `point_transactions` and `point_ledger_entries`, the existing
permissions, audit log and personal WebSocket channels. There is no demo or seeded balance:
every wallet opens at 0 and every figure on the page is read from PostgreSQL.

## Workflow

1. **Wallet overview**: your balance, total issued (net supply), outgoing and incoming
   transfers, points distributed to users, completed transactions and your recent wallet
   activity.
2. **Create Points**: enter a whole-point amount and a reason, credit it to **My wallet**
   (type *Issue*) or to **Another account** found by name, username, email, role or ID
   (type *Credit*). The current balance is shown before confirming.
3. **Transfer Points**: send from **My wallet** or, with `points.view_all`, from **Another
   account**, to any active account in the tenant (Players, Agents, Master Agents,
   Declarators, Operators, other Admins). Both balances are shown; remarks are optional.
   The client blocks zero, fractional, over-balance and same-wallet transfers before the
   confirmation; the server checks everything again under row locks.
4. **Adjust**: audited credit or debit against the issuance account; reason required.
5. Every success shows a **receipt** with the reference and both balances before and after,
   and refetches balances. Nothing is updated optimistically.
6. **All wallets** lists every balance; **Transaction history** filters by type, status,
   date range (Asia/Manila), sender and recipient, searches by username, email, account ID or
   reference, paginates, opens a details dialog (with **Refund transfer** for authorized
   Admins) and exports the filtered rows to CSV.

Only Admin accounts can create, transfer, adjust or refund points, each with its permission.
Master Agents, Agents and Players see their balances and history but no operations; the API
refuses them (`403`) even if a custom role grants the permission.

## Guarantees

| Requirement | Mechanism |
| --- | --- |
| Atomic transfers | both accounts locked in ID order, both ledger entries, both balances, audit and notification in one PostgreSQL transaction; any error rolls back all of it |
| No overspend | `SELECT … FOR UPDATE` balance check plus `CHECK (balance >= 0)`; 10 concurrent transfers against one wallet are tested |
| No duplicates | `UNIQUE (tenant_id, idempotency_key)`; retries replay the original (`Idempotent-Replay: true`); the UI keeps the key only for an unchanged retry after a lost response |
| Immutable history | app role has `SELECT, INSERT` only; triggers refuse `UPDATE`/`DELETE` for every role, including the schema owner |
| Balances only via the ledger | deferred trigger: a changed balance must match a ledger entry written in the same transaction; `GET /admin/points/integrity` reconciles every account |
| New accounts at 0 | wallets are created at 0 and a trigger refuses any other opening balance |
| Tenant isolation | row-level security, composite tenant foreign keys, host-bound tokens |
| Integer accounting | `bigint` points, strict JSON integers, ≤ 1,000,000,000 per operation |

Points are separate from simulated betting play balances; nothing converts one into the
other. The non-monetary notice on the page is unchanged.

See [api.md](../api.md#admin-points-management) for the endpoints, [database.md](../database.md#ledger-invariants)
for the guards and [testing.md](../testing.md) for coverage.

Screenshots from the e2e run (synthetic test tenant): [320px](wallet-management-320.png),
[768px](wallet-management-768.png), [1440px](wallet-management-1440.png).
