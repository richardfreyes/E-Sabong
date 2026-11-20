"""Structured security events: one JSON line per event on the ``security`` logger.

Fields: event, rule, action (observed | would_refuse | refused | blocked | unblocked), client_ip,
method, path (never the query string: it can carry ?token=), class, status, env, request_id
(bound by RequestContextMiddleware), plus rule-specific counts. Never tokens, passwords, cookies,
bodies or payment data.
"""

import time
from typing import Any

import structlog

log = structlog.get_logger("security")

_last_store_warning = 0.0


def emit(event: str, *, env: str, **fields: Any) -> None:
    path = fields.get("path")
    if isinstance(path, str):
        fields["path"] = path[:200]
    log.warning(event, env=env, **fields)


def store_unavailable(env: str, error: BaseException) -> None:
    """At most one line a minute per process: a Redis outage must not flood the logs."""
    global _last_store_warning
    now = time.monotonic()
    if now - _last_store_warning >= 60:
        _last_store_warning = now
        log.warning("security_store_unavailable", env=env, action="fail_open", error=type(error).__name__)


def sampled(over_by: int) -> bool:
    """Log the first request over a limit, then the 10th, 100th, and every 1000th: one flooding
    client cannot turn the security log into a second flood."""
    return over_by in (1, 10, 100) or (over_by > 0 and over_by % 1000 == 0)
