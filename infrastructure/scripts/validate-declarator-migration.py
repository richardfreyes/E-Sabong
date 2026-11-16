"""Run 0040 up/down/up on a RESTORED scratch database; never on a live database.

Run inside the API image, whose environment supplies role credentials:
  python validate-declarator-migration.py esabong_restore_declarator_<run> /tmp/0040.py
Only non-sensitive counts/hashes and timings are printed.
"""

import asyncio
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


async def main(scratch: str, migration: str) -> None:
    settings = get_settings()
    source = make_url(settings.database_url_migrations)
    if not re.fullmatch(re.escape(source.database or "") + r"_restore_declarator_[a-zA-Z0-9_]+", scratch):
        raise ValueError("Only a dedicated restored Declarator scratch database is allowed")
    owner = create_async_engine(source.set(database=scratch))
    reader = create_async_engine(make_url(settings.database_url_platform).set(database=scratch))
    spec = importlib.util.spec_from_file_location("receipt_migration", Path(migration))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def snapshot():
        async with reader.connect() as db:
            names = (
                await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))
            ).scalars()
            facts = {}
            for name in names:
                if name in ("alembic_version", "dummy_stake_receipts"):
                    continue
                assert re.fullmatch(r"[a-z_]+", name)
                columns = "to_jsonb(t) - 'dummy_request_ids'" if name == "fight_books" else "to_jsonb(t)"
                facts[name] = (
                    (
                        await db.execute(
                            text(
                                f"SELECT count(*), md5(coalesce(string_agg(md5(({columns})::text), '' "  # noqa: S608 - identifiers validated above
                                f"ORDER BY md5(({columns})::text)),'')) FROM public.{name} t"
                            )
                        )
                    )
                    .one()
                    ._asdict()
                )
            return facts

    async def keys(receipts: bool):
        async with reader.connect() as db:
            sql = (
                "SELECT tenant_id::text, match_id::text, request_id::text FROM dummy_stake_receipts ORDER BY 1,2,3"
                if receipts
                else "SELECT tenant_id::text, match_id::text, unnest(dummy_request_ids)::text FROM fight_books ORDER BY 1,2,3"
            )
            return sorted(set((await db.execute(text(sql))).all()))

    async def apply(direction: str, revision: str):
        begin = time.perf_counter()
        async with owner.begin() as db:

            def execute(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    getattr(module, direction)()
                connection.execute(
                    text("UPDATE alembic_version SET version_num=:revision"),
                    {"revision": revision},
                )

            await db.run_sync(execute)
        await reader.dispose()
        return round((time.perf_counter() - begin) * 1000, 2)

    try:
        async with owner.connect() as db:
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0039":
            raise ValueError("Trial requires a restored 0039 snapshot")
        before = await snapshot()
        legacy = await keys(False)
        upgraded = await apply("upgrade", "0040")
        assert await snapshot() == before, "Stored application/financial/audit values changed on upgrade"
        assert await keys(True) == legacy, "Legacy retry IDs not preserved"
        async with reader.connect() as db:
            assert await db.scalar(text("SELECT sum(cardinality(dummy_request_ids)) FROM fight_books")) in (None, 0)
            policy = (
                await db.execute(
                    text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='dummy_stake_receipts'")
                )
            ).one()
            assert policy == (True, True)
        downgraded = await apply("downgrade", "0039")
        assert await snapshot() == before, "Stored application/financial/audit values changed on downgrade"
        assert await keys(False) == legacy, "Rollback lost retry IDs"
        reapplied = await apply("upgrade", "0040")
        assert await snapshot() == before and await keys(True) == legacy
        print(
            json.dumps(
                {
                    "passed": True,
                    "scratch": scratch,
                    "application_tables_verified": len(before),
                    "retry_ids_preserved": len(legacy),
                    "upgrade_ms": upgraded,
                    "downgrade_ms": downgraded,
                    "reapply_ms": reapplied,
                    "final_revision": "0040",
                    "forced_rls": True,
                }
            )
        )
    finally:
        await owner.dispose()
        await reader.dispose()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
