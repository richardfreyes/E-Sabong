"""Persistent browser subscriptions cannot exhaust authentication/cache capacity."""

import asyncio

from app.core.redis import get_pubsub_redis, get_redis


async def test_subscriptions_leave_command_pool_available(app):  # type: ignore[no-untyped-def]
    commands = get_redis()
    subscriptions = get_pubsub_redis()
    sockets = [subscriptions.pubsub() for _ in range(120)]
    try:
        await asyncio.gather(*(socket.subscribe(f"capacity-test:{i}") for i, socket in enumerate(sockets)))
        assert await asyncio.wait_for(commands.ping(), timeout=2)
        assert commands.connection_pool is not subscriptions.connection_pool
    finally:
        await asyncio.gather(*(socket.aclose() for socket in sockets))


async def test_command_pool_waits_for_released_capacity(app):  # type: ignore[no-untyped-def]
    from redis.asyncio import BlockingConnectionPool, Redis

    from app.core.config import get_settings

    pool = BlockingConnectionPool.from_url(get_settings().redis_url, max_connections=1, timeout=2)
    client = Redis(connection_pool=pool)
    held = await pool.get_connection()
    ping = asyncio.create_task(client.ping())
    try:
        await asyncio.sleep(0.05)
        assert not ping.done()
        await pool.release(held)
        held = None
        assert await ping
    finally:
        ping.cancel()
        await asyncio.gather(ping, return_exceptions=True)
        if held is not None:
            await pool.release(held)
        await client.aclose(close_connection_pool=True)
