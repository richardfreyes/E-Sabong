"""Who is connected right now: player WebSocket connections per tenant, kept in Redis.

Each player connection writes ``<user_id>:<connection_id>`` into a sorted set scored by the last
heartbeat. A connection that stops heartbeating (crash, network loss) drops out after
``PRESENCE_WINDOW_SECONDS``; a clean disconnect removes it at once. One player with two tabs counts once.
"""

import time
import uuid

from app.core.redis import get_redis

PRESENCE_WINDOW_SECONDS = 75


def _key(tenant_id: uuid.UUID) -> str:
    return f"presence:{tenant_id}:players"


async def touch(tenant_id: uuid.UUID, user_id: uuid.UUID, connection: str) -> None:
    redis = get_redis()
    key = _key(tenant_id)
    await redis.zadd(key, {f"{user_id}:{connection}": time.time()})
    await redis.expire(key, PRESENCE_WINDOW_SECONDS * 2)


async def leave(tenant_id: uuid.UUID, user_id: uuid.UUID, connection: str) -> None:
    await get_redis().zrem(_key(tenant_id), f"{user_id}:{connection}")


async def online_players(tenant_id: uuid.UUID) -> int:
    redis = get_redis()
    key = _key(tenant_id)
    await redis.zremrangebyscore(key, "-inf", time.time() - PRESENCE_WINDOW_SECONDS)
    members = await redis.zrange(key, 0, -1)
    return len({str(m).split(":", 1)[0] for m in members})
