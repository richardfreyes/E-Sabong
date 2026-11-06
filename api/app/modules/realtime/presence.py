"""Who is connected right now: player WebSocket connections per tenant, kept in Redis.

Each player connection writes ``<user_id>:<connection_id>`` into a sorted set scored by the last
heartbeat. A connection that stops heartbeating (crash, network loss) drops out after
``PRESENCE_WINDOW_SECONDS``; a clean disconnect removes it at once. One player with two tabs counts once.
"""

import time
import uuid

from app.core.redis import get_redis

PRESENCE_WINDOW_SECONDS = 75


def _key(tenant_id: uuid.UUID, scope: str = "players") -> str:
    return f"presence:{tenant_id}:{scope}"


async def touch(tenant_id: uuid.UUID, user_id: uuid.UUID, connection: str, *, player: bool = True) -> None:
    """Heartbeat one connection. Every account is tracked under "accounts"; players also under "players"."""
    redis = get_redis()
    member, now = f"{user_id}:{connection}", time.time()
    for scope in ("players", "accounts") if player else ("accounts",):
        key = _key(tenant_id, scope)
        await redis.zadd(key, {member: now})
        await redis.expire(key, PRESENCE_WINDOW_SECONDS * 2)


async def leave(tenant_id: uuid.UUID, user_id: uuid.UUID, connection: str) -> None:
    redis = get_redis()
    for scope in ("players", "accounts"):
        await redis.zrem(_key(tenant_id, scope), f"{user_id}:{connection}")


async def online_player_ids(tenant_id: uuid.UUID, scope: str = "players") -> dict[uuid.UUID, float]:
    """Connected player ids (``scope="accounts"``: any account), each with the latest heartbeat
    (unix seconds) across their connections."""
    redis = get_redis()
    key = _key(tenant_id, scope)
    await redis.zremrangebyscore(key, "-inf", time.time() - PRESENCE_WINDOW_SECONDS)
    seen: dict[uuid.UUID, float] = {}
    entries: list[tuple[str, float]] = await redis.zrange(key, 0, -1, withscores=True)  # type: ignore[assignment]
    for member, score in entries:
        raw = str(member)
        try:
            user_id = uuid.UUID(raw.split(":", 1)[0])
        except ValueError:
            continue
        seen[user_id] = max(seen.get(user_id, 0.0), float(score))
    return seen


async def online_players(tenant_id: uuid.UUID) -> int:
    return len(await online_player_ids(tenant_id))
