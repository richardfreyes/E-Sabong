"""Per-request server timing: total time, SQL statement time and statement count.

Sent as a ``Server-Timing`` header, so browser DevTools (Network → Timing) split a slow request into
queueing/network on the client side and application vs database time on the server side, and
written to the request log line. SQL time is measured around each statement's execution, so it
includes waiting for row locks and for the event loop to resume the request (CPU starvation shows
up as app time well above SQL time).
"""

import time
from contextvars import ContextVar
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine


class RequestTiming:
    __slots__ = ("db_count", "db_seconds", "started")

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.db_seconds = 0.0
        self.db_count = 0

    def header(self, total_ms: float) -> str:
        return f'app;dur={total_ms:.1f}, db;dur={self.db_seconds * 1000:.1f};desc="{self.db_count} queries"'


_current: ContextVar[RequestTiming | None] = ContextVar("request_timing", default=None)


def begin() -> RequestTiming:
    """Start timing this request. The holder is shared by reference with tasks the request spawns."""
    timing = RequestTiming()
    _current.set(timing)
    return timing


def instrument(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        if context is not None:
            context._esb_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        timing = _current.get()
        started = getattr(context, "_esb_started", None)
        if timing is not None and started is not None:
            timing.db_seconds += time.perf_counter() - started
            timing.db_count += 1
