"""Read-only financial fingerprint of one environment, for before/after comparisons around a release.

    docker compose exec -T api python - < infrastructure/scripts/financial-fingerprint.py > before.json
    ... deploy ...
    docker compose exec -T api python - < infrastructure/scripts/financial-fingerprint.py > after.json
    python3 infrastructure/scripts/financial-fingerprint.py compare before.json after.json

Runs in ONE read-only, repeatable-read transaction (writes nothing, takes no locks beyond reads):
per tenant the betting reconciliation (wallets vs ledger, pool/wallet mismatches, totals) and, for
every row of the financial tables, md5(row). `compare` (host side, stdlib only) reports rows that
changed or disappeared, rows added after the first snapshot, and any reconciliation that is no
longer balanced. Prints no personal data: ids and hashes only.
"""

import json
import sys

TABLES = (
    "play_wallets",
    "play_wallet_entries",
    "bets",
    "fight_books",
    "commission_entries",
    "match_results",
    "match_result_history",
    "result_declarations",
    "dummy_stake_receipts",
    "audit_logs",
)


def compare(before_path: str, after_path: str) -> int:
    before, after = json.load(open(before_path)), json.load(open(after_path))
    out: dict = {"before_at": before["at"], "after_at": after["at"], "tables": {}, "tenants": {}}
    bad = False
    for table, rows in before["rows"].items():
        now = after["rows"].get(table, {})
        changed = [k for k, v in rows.items() if k in now and now[k] != v]
        missing = [k for k in rows if k not in now]
        added = len(set(now) - set(rows))
        out["tables"][table] = {"before": len(rows), "changed": len(changed), "missing": len(missing), "added": added}
        if changed or missing:
            out["tables"][table]["changed_ids"] = (changed + missing)[:20]
            bad = bad or table != "play_wallets"  # wallet rows change with every bet; judged by reconciliation
    for tenant, rec in after["reconciliation"].items():
        prev = before["reconciliation"].get(tenant, {})
        out["tenants"][tenant] = {
            "balanced_before": prev.get("balanced"),
            "balanced_after": rec.get("balanced"),
            "checks_after": rec.get("checks"),
        }
        if prev.get("balanced") and not rec.get("balanced"):
            bad = True
    out["passed"] = not bad
    print(json.dumps(out, indent=2))
    return 0 if not bad else 1


async def snapshot() -> None:
    from datetime import UTC, datetime

    from app.core.database import dispose_engines, platform_session
    from app.modules.betting.reports import reconciliation
    from app.modules.tenants.models import Tenant
    from sqlalchemy import text

    result: dict = {"at": datetime.now(UTC).isoformat(), "rows": {}, "reconciliation": {}}
    async with platform_session() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for table in TABLES:
            if await db.scalar(text("SELECT to_regclass(:name)"), {"name": table}):
                rows = await db.execute(text(f"SELECT id::text, md5(to_jsonb(t)::text) FROM {table} t"))  # noqa: S608 - constant allowlist
                result["rows"][table] = dict(rows.all())
        for tenant in (await db.scalars(__import__("sqlalchemy").select(Tenant))).all():
            rec = await reconciliation(db, tenant.id)
            data = rec.model_dump(mode="json")
            result["reconciliation"][str(tenant.id)] = {
                "balanced": data.get("balanced"),
                "checks": data.get("checks"),
                "staked": data.get("staked"),
                "in_wallets": data.get("in_wallets"),
                "paid_to_winners": data.get("paid_to_winners"),
                "refunded": data.get("refunded"),
            }
        await db.rollback()
    await dispose_engines()
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    import asyncio

    asyncio.run(snapshot())
