"""Integration test fixtures.

Tests run against real PostgreSQL and Redis (``docker compose up -d postgres redis``)
using the ``esabong_test`` database, migrated from scratch once per session, and
Redis database 15. Tenants are addressed by Host header exactly as in production.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ---- environment must be configured before the app is imported -----------------
_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost:5432")
_DB_NAME = os.environ.get("TEST_DB_NAME", "esabong_test")
_REDIS = os.environ.get("TEST_REDIS_URL", "redis://:dev-redis@localhost:6379")
_PASSWORDS = {
    "app": os.environ.get("TEST_DB_APP_PASSWORD", "dev-app"),
    "platform": os.environ.get("TEST_DB_PLATFORM_PASSWORD", "dev-platform"),
    "owner": os.environ.get("TEST_DB_OWNER_PASSWORD", "dev-owner"),
}

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

_key = Ed25519PrivateKey.generate()
_PRIVATE_PEM = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
_PUBLIC_PEM = _key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "BASE_DOMAIN": "test.local",
        "PLATFORM_HOST": "owner.test.local",
        "PUBLIC_SCHEME": "http",
        "ALLOW_TENANT_HEADER": "false",
        "DATABASE_URL_APP": f"postgresql+asyncpg://esabong_app:{_PASSWORDS['app']}@{_DB_HOST}/{_DB_NAME}",
        "DATABASE_URL_PLATFORM": f"postgresql+asyncpg://esabong_platform:{_PASSWORDS['platform']}@{_DB_HOST}/{_DB_NAME}",
        "DATABASE_URL_MIGRATIONS": f"postgresql+asyncpg://esabong_owner:{_PASSWORDS['owner']}@{_DB_HOST}/{_DB_NAME}",
        "DB_POOL_SIZE": "20",
        "DB_MAX_OVERFLOW": "40",
        "REDIS_URL": f"{_REDIS}/15",
        "CELERY_BROKER_URL": f"{_REDIS}/14",
        "CELERY_RESULT_BACKEND": f"{_REDIS}/14",
        "JWT_SECRET": "test-secret-0123456789abcdef0123456789abcdef",
        "COOKIE_SECURE": "false",
        "MEDIA_ROOT": str(Path(__file__).parent / ".media"),
        "LICENSE_PRIVATE_KEY_PEM": _PRIVATE_PEM,
        "LICENSE_PUBLIC_KEY_PEM": _PUBLIC_PEM,
        "LOGIN_MAX_ATTEMPTS": "5",
    }
)

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import platform_session  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.modules.auth.models import PlatformRole, PlatformUser  # noqa: E402
from app.modules.subscriptions.models import PlanKind, SubscriptionPlan  # noqa: E402
from app.modules.tenants.models import ALL_MODULES  # noqa: E402

PASSWORD = "Str0ng!Passw0rd"
API_DIR = Path(__file__).resolve().parents[1]


def pytest_sessionstart(session: pytest.Session) -> None:
    env = {**os.environ, "ALEMBIC_DATABASE_URL": os.environ["DATABASE_URL_MIGRATIONS"]}
    alembic = [sys.executable, "-m", "alembic"]
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([*alembic, *args], cwd=API_DIR, env=env, check=True, capture_output=True)


@pytest.fixture(scope="session")
async def app():  # type: ignore[no-untyped-def]
    from app.main import app as fastapi_app

    await get_redis().flushdb()
    with __import__("unittest.mock").mock.patch("app.workers.tasks.email.send_email.delay"):
        yield fastapi_app
    from app.core.database import dispose_engines
    from app.core.redis import close_redis

    await dispose_engines()
    await close_redis()


def host_for(slug: str) -> str:
    return f"{slug}.test.local"


def client_for(app, host: str, token: str | None = None) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}", headers=headers, timeout=60)


def unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def plans(app) -> dict[str, SubscriptionPlan]:  # type: ignore[no-untyped-def]
    out = {}
    async with platform_session() as db:
        for code, limits, modules, trial in (
            ("test-pro", {}, list(ALL_MODULES), 14),
            ("test-small", {"max_users": 5, "max_events_per_month": 2}, ["events", "matches"], 0),
            ("test-big", {}, list(ALL_MODULES), 0),
        ):
            existing = (await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == code))).scalar_one_or_none()
            if existing is None:
                price = {"test-pro": 10000, "test-small": 3000, "test-big": 30000}[code]
                existing = SubscriptionPlan(
                    code=code,
                    name=code.title(),
                    kind=PlanKind.SAAS,
                    price_monthly_cents=price,
                    price_annual_cents=price * 10,
                    currency="PHP",
                    trial_days=trial,
                    limits=limits,
                    modules=modules,
                )
                db.add(existing)
                await db.flush()
            out[code] = existing
    return out


@dataclass
class PlatformHandle:
    client: httpx.AsyncClient
    user_id: uuid.UUID
    email: str


async def make_platform_user(app, role: PlatformRole = PlatformRole.SUPER_ADMIN) -> PlatformHandle:  # type: ignore[no-untyped-def]
    email = f"{unique('ops')}@example.com"
    async with platform_session() as db:
        user = PlatformUser(email=email, full_name="Ops", role=role, password_hash=hash_password(PASSWORD))
        db.add(user)
        await db.flush()
        uid = user.id
    client = client_for(app, "owner.test.local")
    r = await client.post("/api/v1/platform/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return PlatformHandle(client=client, user_id=uid, email=email)


@pytest.fixture
async def owner(app) -> AsyncIterator[PlatformHandle]:  # type: ignore[no-untyped-def]
    handle = await make_platform_user(app)
    yield handle
    await handle.client.aclose()


@dataclass
class TenantHandle:
    app: object
    id: uuid.UUID
    slug: str
    host: str
    admin: httpx.AsyncClient
    admin_id: uuid.UUID
    clients: list[httpx.AsyncClient] = field(default_factory=list)

    async def login(self, username: str, password: str = PASSWORD) -> httpx.AsyncClient:
        c = client_for(self.app, self.host)
        r = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self.clients.append(c)
        return c

    async def create_user(self, username: str, account_type: str, parent_id: str | None = None, via=None, **extra) -> dict:  # type: ignore[no-untyped-def]
        client = via or self.admin
        body = {
            "username": username,
            "full_name": username.title(),
            "account_type": account_type,
            "parent_id": parent_id,
            "password": PASSWORD,
            **extra,
        }
        r = await client.post("/api/v1/admin/users", json=body)
        assert r.status_code == 201, r.text
        return r.json()["user"]


async def make_tenant(app, owner: PlatformHandle, plan: SubscriptionPlan, *, with_trial: bool = False) -> TenantHandle:  # type: ignore[no-untyped-def]
    slug = unique("t")
    r = await owner.client.post(
        "/api/v1/platform/tenants",
        json={
            "name": f"Tenant {slug}",
            "slug": slug,
            "plan_id": str(plan.id),
            "billing_cycle": "monthly",
            "with_trial": with_trial,
            "admin_email": f"admin@{slug}.example.com",
            "admin_name": "Admin",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    host = host_for(slug)
    admin = client_for(app, host)
    r = await admin.post("/api/v1/auth/login", json={"username": "admin", "password": data["temporary_password"]})
    assert r.status_code == 200, r.text
    admin.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    r = await admin.post(
        "/api/v1/auth/me/password", json={"current_password": data["temporary_password"], "new_password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    me = (await admin.get("/api/v1/auth/me")).json()
    return TenantHandle(
        app=app, id=uuid.UUID(data["tenant"]["id"]), slug=slug, host=host, admin=admin, admin_id=uuid.UUID(me["id"])
    )


@pytest.fixture
async def tenant(app, owner, plans) -> AsyncIterator[TenantHandle]:  # type: ignore[no-untyped-def]
    t = await make_tenant(app, owner, plans["test-pro"])
    yield t
    for c in [t.admin, *t.clients]:
        await c.aclose()


@pytest.fixture
async def other_tenant(app, owner, plans) -> AsyncIterator[TenantHandle]:  # type: ignore[no-untyped-def]
    t = await make_tenant(app, owner, plans["test-pro"])
    yield t
    for c in [t.admin, *t.clients]:
        await c.aclose()
