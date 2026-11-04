"""Read-only report on the retired points ledger (dry run, before/after evidence).

The transferable points module is retired: nothing writes to ``point_accounts``,
``point_transactions`` or ``point_ledger_entries`` any more (migration 0013 enforces it). This
report reads those tables across all tenants and checks that the archive is internally consistent.
It never changes a row, and it never adds points to Arena Points (or anything else).

    python -m app.cli points-archive-report [--json path]
"""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PER_TENANT = text(
    """
    SELECT t.slug,
           (SELECT count(*) FROM point_accounts a WHERE a.tenant_id = t.id) AS accounts,
           (SELECT count(*) FROM point_accounts a WHERE a.tenant_id = t.id AND a.balance <> 0) AS nonzero_accounts,
           (SELECT coalesce(sum(a.balance), 0) FROM point_accounts a
               WHERE a.tenant_id = t.id AND a.kind = 'user') AS user_balances,
           (SELECT coalesce(sum(a.balance), 0) FROM point_accounts a WHERE a.tenant_id = t.id) AS net_balance,
           (SELECT count(*) FROM point_transactions x WHERE x.tenant_id = t.id) AS transactions,
           (SELECT count(*) FROM point_ledger_entries e WHERE e.tenant_id = t.id) AS ledger_entries,
           (SELECT max(x.created_at) FROM point_transactions x WHERE x.tenant_id = t.id) AS last_activity,
           -- accounts whose stored balance differs from the sum of their ledger entries
           (SELECT count(*) FROM point_accounts a WHERE a.tenant_id = t.id AND a.balance <>
               coalesce((SELECT sum(e.delta) FROM point_ledger_entries e
                   WHERE e.account_id = a.id AND e.tenant_id = t.id), 0)) AS balance_mismatches,
           -- missing entries, wrong amounts, or entries that do not sum to zero
           (SELECT count(*) FROM (
               SELECT x.id FROM point_transactions x LEFT JOIN point_ledger_entries e
                   ON e.transaction_id = x.id AND e.tenant_id = t.id
               WHERE x.tenant_id = t.id GROUP BY x.id, x.amount
               HAVING count(e.id) <> 2 OR coalesce(sum(e.delta), 0) <> 0
                   OR coalesce(max(e.delta), 0) <> x.amount
           ) z) AS unbalanced_transactions
    FROM tenants t
    ORDER BY t.slug
    """
)


@dataclass(frozen=True)
class TenantArchive:
    slug: str
    accounts: int
    nonzero_accounts: int
    user_balances: int
    net_balance: int
    transactions: int
    ledger_entries: int
    last_activity: datetime | None
    balance_mismatches: int
    unbalanced_transactions: int
    accounts_sha256: str = ""
    transactions_sha256: str = ""
    entries_sha256: str = ""

    @property
    def consistent(self) -> bool:
        return self.net_balance == 0 and self.balance_mismatches == 0 and self.unbalanced_transactions == 0


async def report(db: AsyncSession) -> list[TenantArchive]:
    """Every tenant's archived points ledger. Needs a session that sees all tenants (platform engine)."""
    rows = []
    for row in (await db.execute(PER_TENANT)).all():
        data = dict(row._mapping)
        rows.append(TenantArchive(**{k: (v if k in ("slug", "last_activity") else int(v)) for k, v in data.items()}))
    # Hash every stored column, including metadata, in stable UUID order. Counts and sums alone
    # cannot detect an edited reference, memo or a pair of changes with a net delta of zero.
    by_slug = {row.slug: row for row in rows}
    digests: dict[str, dict[str, str]] = {slug: {} for slug in by_slug}
    for table, field in (
        ("point_accounts", "accounts_sha256"),
        ("point_transactions", "transactions_sha256"),
        ("point_ledger_entries", "entries_sha256"),
    ):
        hashes = {slug: sha256() for slug in by_slug}
        records = await db.stream(
            text(
                f"SELECT t.slug, row_to_json(a)::text FROM {table} a "  # noqa: S608 -- fixed table allowlist above
                "JOIN tenants t ON t.id = a.tenant_id ORDER BY t.slug, a.id"
            )
        )
        async for slug, payload in records:
            hashes[slug].update(payload.encode("utf-8") + b"\n")
        for slug, digest in hashes.items():
            digests[slug][field] = digest.hexdigest()
    return [replace(row, **digests[row.slug]) for row in rows]


def fingerprint(rows: list[TenantArchive]) -> list[dict]:  # type: ignore[type-arg]
    """Comparable form for before/after checks (JSON-safe)."""
    out = []
    for r in rows:
        d = asdict(r)
        d["last_activity"] = r.last_activity.isoformat() if r.last_activity else None
        out.append(d)
    return out
