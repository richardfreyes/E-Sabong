import logging
import re
import sys
import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

_QUERY_SECRET = re.compile(r"(?i)([?&](?:access_token|refresh_token|token)=)[^&\s\"']+")


def redact(value: object) -> object:
    """Replace credential query values (the WebSocket ``?token=``) in a log string."""
    return _QUERY_SECRET.sub(r"\1[redacted]", value) if isinstance(value, str) else value


class RedactCredentials(logging.Filter):
    """Uvicorn logs WebSocket handshakes with the full query string; never write the token.

    Applied to the uvicorn loggers. It rewrites the record before formatting, so the line and the
    connection itself are unchanged apart from the credential value."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) for k, v in record.args.items()}
        return True


def install_redaction() -> None:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactCredentials) for f in target.filters):
            target.addFilter(RedactCredentials())


def configure_logging() -> None:
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    renderer: structlog.types.Processor = structlog.processors.JSONRenderer() if s.log_json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    install_redaction()


class RequestContextMiddleware:
    """Assigns a request id, binds it to the log context and logs one line per request.

    Pure ASGI rather than BaseHTTPMiddleware: no extra task group and memory stream per request, and
    the log context and request timing are visible to the endpoint because it runs in this task.
    Response headers carry the request id and ``Server-Timing`` (app time, SQL time, query count)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from app.core import timing as request_timing

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if 8 <= len(incoming) <= 64 and incoming.isascii() else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        timing = request_timing.begin()
        status = 500

        async def send_with_context(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Server-Timing"] = timing.header((time.perf_counter() - start) * 1000)
            await send(message)

        await self.app(scope, receive, send_with_context)
        state = scope.get("state") or {}
        client = scope.get("client")
        structlog.get_logger("http").info(
            "request",
            method=scope["method"],
            path=scope["path"],
            status=status,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            db_ms=round(timing.db_seconds * 1000, 1),
            db_queries=timing.db_count,
            tenant=state.get("tenant_slug"),
            # Address written by the trusted gateway (uvicorn --proxy-headers) and the edge-guard class.
            client_ip=client[0] if client else None,
            security_class=state.get("security_class"),
        )
