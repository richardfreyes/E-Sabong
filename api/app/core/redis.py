from redis.asyncio import BlockingConnectionPool, Redis

from app.core.config import get_settings

_client: Redis | None = None
_pubsub_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        s = get_settings()
        _client = Redis(
            connection_pool=BlockingConnectionPool.from_url(
                s.redis_url,
                decode_responses=True,
                max_connections=s.redis_max_connections,
                timeout=s.redis_pool_timeout_seconds,
            )
        )
    return _client


def get_pubsub_redis() -> Redis:
    """Long-lived subscriptions must not consume API command-pool capacity."""
    global _pubsub_client
    if _pubsub_client is None:
        s = get_settings()
        _pubsub_client = Redis.from_url(
            s.redis_url,
            decode_responses=True,
            max_connections=s.redis_pubsub_max_connections,
        )
    return _pubsub_client


async def close_redis() -> None:
    global _client, _pubsub_client
    if _client is not None:
        await _client.aclose(close_connection_pool=True)
        _client = None
    if _pubsub_client is not None:
        await _pubsub_client.aclose(close_connection_pool=True)
        _pubsub_client = None
