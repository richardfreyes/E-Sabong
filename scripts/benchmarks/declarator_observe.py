"""Read-only server-side WS/pool observations for the isolated real soak."""

import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main(state_path: str, output_path: str, result_path: str) -> None:
    state = json.loads(await asyncio.to_thread(Path(state_path).read_text))
    if not state["database"].startswith("esabong_test_declarator_"):
        raise ValueError("Only dedicated Declarator test runtimes are observable")
    environment = state["environment"]
    for key in ("DATABASE_URL_PLATFORM", "REDIS_URL"):
        if urlsplit(environment[key]).hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("Local test services only")
    redis = Redis.from_url(environment["REDIS_URL"], decode_responses=True)
    engine = create_async_engine(environment["DATABASE_URL_PLATFORM"], pool_size=1, max_overflow=0)
    started = time.monotonic()
    try:
        async with engine.connect() as db:
            tenant = await db.scalar(text("SELECT id FROM tenants WHERE slug='soak'"))
        while not await asyncio.to_thread(Path(result_path).exists):
            async with engine.connect() as db:
                pools = (
                    await db.execute(
                        text("SELECT state,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY state")
                    )
                ).all()
                jobs = await db.scalar(text("SELECT count(*) FROM ghost_auto_bets WHERE status='active'"))
                legacy = await db.scalar(text("SELECT coalesce(max(cardinality(dummy_request_ids)),0) FROM fight_books"))
            subscriptions = await redis.pubsub_numsub(f"rt:tenant:{tenant}")
            counts = {}
            for scope in ("players", "accounts"):
                counts[scope] = await redis.zcard(f"presence:{tenant}:{scope}")
            sample = {
                "at": time.time(),
                "elapsedSeconds": round(time.monotonic() - started, 2),
                "tenantSubscribers": subscriptions[0][1],
                "presenceConnections": counts,
                "databaseConnections": dict(pools),
                "activeGhostJobs": jobs,
                "maxLegacyRetryArray": legacy,
            }

            def append(sample=sample):
                with Path(output_path).open("a") as file:
                    file.write(json.dumps(sample) + "\n")

            await asyncio.to_thread(append)
            await asyncio.sleep(30)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
