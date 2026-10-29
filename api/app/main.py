import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

import app.models  # noqa: F401 - register all mappers
from app.core.config import get_settings
from app.core.database import dispose_engines, get_engine
from app.core.errors import install_error_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.redis import close_redis, get_redis
from app.modules.audit.router import platform_router as platform_audit_router
from app.modules.audit.router import tenant_router as tenant_audit_router
from app.modules.auth.platform_router import router as platform_auth_router
from app.modules.auth.router import router as auth_router
from app.modules.declarator.router import router as declarator_router
from app.modules.demo_points.router import router as points_router
from app.modules.events.router import router as events_router
from app.modules.licenses.router import licensing_router, status_router
from app.modules.licenses.router import router as platform_licenses_router
from app.modules.licenses.service import current_license_state
from app.modules.portal.router import router as portal_router
from app.modules.realtime.router import router as ws_router
from app.modules.reports.router import router as reports_router
from app.modules.subscriptions.router import router as billing_router
from app.modules.tenants.admin_router import router as tenant_settings_router
from app.modules.tenants.platform_router import router as platform_tenants_router
from app.modules.tenants.public_router import internal_router
from app.modules.tenants.public_router import router as public_router
from app.modules.users.router import router as users_router

API_VERSION = "0.1.0"
USAGE_TTL_SECONDS = 3 * 24 * 3600


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/v1/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response


class UsageMeterMiddleware(BaseHTTPMiddleware):
    """Counts tenant API requests per day in Redis; the worker persists them."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is not None:
            key = f"usage:{tenant_id}:{datetime.now(UTC).date().isoformat()}:api_requests"
            try:
                redis = get_redis()
                if await redis.incr(key) == 1:
                    await redis.expire(key, USAGE_TTL_SECONDS)
            except Exception:  # noqa: BLE001, S110 - metering must never break a request
                pass
        return response


class LicenseEnforcementMiddleware(BaseHTTPMiddleware):
    """Dedicated installs refuse API traffic without a valid signed license."""

    EXEMPT = ("/api/health", "/api/ready", "/api/v1/license/status", "/api/v1/public/tenant", "/api/docs", "/api/openapi.json")

    def __init__(self, app, ttl: float = 60.0) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._ttl = ttl
        self._cached: tuple[float, dict] | None = None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(self.EXEMPT):
            return await call_next(request)
        now = time.monotonic()
        if self._cached is None or now - self._cached[0] > self._ttl:
            self._cached = (now, await current_license_state())
        state = self._cached[1]
        if not state["valid"]:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "license_invalid",
                        "message": state["reason"],
                        "details": {},
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engines()
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=API_VERSION,
        description=(
            "Multi-tenant event and match management. Tenant endpoints resolve the workspace "
            "from the request host. Demonstration points are non-monetary and unrelated to match outcomes."
        ),
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    if settings.deployment_mode == "dedicated":
        app.add_middleware(LicenseEnforcementMiddleware)
    app.add_middleware(UsageMeterMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestContextMiddleware)

    v1 = APIRouter(prefix="/api/v1")
    for r in (
        public_router,
        internal_router,
        status_router,
        licensing_router,
        platform_auth_router,
        platform_tenants_router,
        billing_router,
        platform_licenses_router,
        platform_audit_router,
        auth_router,
        users_router,
        events_router,
        declarator_router,
        points_router,
        reports_router,
        tenant_settings_router,
        tenant_audit_router,
        portal_router,
    ):
        v1.include_router(r)
    app.include_router(v1)
    app.include_router(ws_router)

    @app.get("/api/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": API_VERSION}

    @app.get("/api/ready", tags=["health"])
    async def ready() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            async with get_engine("app").connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {type(exc).__name__}"
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"
        healthy = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if healthy else 503, content={"status": "ok" if healthy else "degraded", **checks})

    if not settings.is_production:
        # In production Caddy serves media straight from the shared volume.
        Path(settings.media_root).mkdir(parents=True, exist_ok=True)
        app.mount(settings.media_url_prefix, StaticFiles(directory=settings.media_root), name="media")

    return app


app = create_app()
