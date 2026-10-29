"""Bridge between synchronous Celery tasks and the async service layer.

Each task runs in a fresh event loop; asyncpg pools are bound to the loop that
created them, so engines and the Redis client are disposed after every run.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.database import dispose_engines
from app.core.logging import configure_logging
from app.core.redis import close_redis

T = TypeVar("T")
_configured = False


def run_async(fn: Callable[[], Awaitable[T]]) -> T:
    global _configured
    if not _configured:
        configure_logging()
        _configured = True

    async def _wrapper() -> T:
        try:
            return await fn()
        finally:
            await dispose_engines()
            await close_redis()

    return asyncio.run(_wrapper())
