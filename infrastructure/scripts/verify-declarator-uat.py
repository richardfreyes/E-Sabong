"""Compare pre-existing financial rows after a synthetic UAT smoke; never mutate them."""

import asyncio
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import dispose_engines, platform_session
from app.core.redis import close_redis


async def main(fixture_path: str) -> None:
    fixture = json.loads(await asyncio.to_thread(Path(fixture_path).read_text))
    settings = get_settings()
    if (
        fixture["origin"] != "https://" + settings.base_domain
        or not settings.base_domain.startswith("uat.")
        or not settings.synthetic_data_enabled
    ):
        raise ValueError("Matching UAT fixture required")
    if not fixture.get("protected"):
        raise ValueError("Protected-row fingerprint snapshot required")
    changed = {}
    total = 0
    async with platform_session() as db:
        for table, before in fixture["protected"].items():
            if not re.fullmatch(r"[a-z_]+", table):
                raise ValueError("Invalid table")
            rows = dict(
                (
                    await db.execute(
                        text(
                            f"SELECT id::text,md5(to_jsonb(t)::text) FROM {table} t "  # noqa: S608 - identifier validated above
                            "WHERE id::text=ANY(CAST(:ids AS text[]))"
                        ),
                        {"ids": list(before)},
                    )
                ).all()
            )
            different = sum(rows.get(key) != value for key, value in before.items())
            total += len(before)
            if different:
                changed[table] = different
    print(
        json.dumps(
            {
                "passed": not changed,
                "protected_rows": total,
                "tables": len(fixture["protected"]),
                "changed_counts": changed,
            }
        )
    )
    await dispose_engines()
    await close_redis()
    if changed:
        raise RuntimeError("Pre-existing UAT financial/audit rows changed; release gate closed")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
