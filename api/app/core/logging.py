import logging
import sys
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings


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


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds it to the log context and logs one line per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if 8 <= len(incoming) <= 64 and incoming.isascii() else uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        structlog.get_logger("http").info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=elapsed_ms,
            tenant=getattr(request.state, "tenant_slug", None),
        )
        return response
