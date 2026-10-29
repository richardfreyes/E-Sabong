"""Database engines and session lifecycle.

Two engines are used on purpose:

* ``app_engine`` connects as a role *without* BYPASSRLS. Every transaction opened
  through a tenant session sets ``app.tenant_id`` locally, and PostgreSQL row-level
  security restricts every tenant table to that tenant. If the context is missing
  the policies match nothing, so a forgotten filter fails closed.
* ``platform_engine`` connects as a BYPASSRLS role and is only reachable from
  platform-owner routes and background workers.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.core.config import get_settings

AfterCommit = Callable[[], Awaitable[Any]]

_engines: dict[str, AsyncEngine] = {}
_factories: dict[str, async_sessionmaker[AsyncSession]] = {}


def _make_engine(url: str) -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC", "application_name": "esabong-api"}},
    )


def get_engine(kind: str) -> AsyncEngine:
    if kind not in _engines:
        s = get_settings()
        url = s.database_url_app if kind == "app" else s.database_url_platform
        _engines[kind] = _make_engine(url)
    return _engines[kind]


def get_session_factory(kind: str) -> async_sessionmaker[AsyncSession]:
    if kind not in _factories:
        _factories[kind] = async_sessionmaker(get_engine(kind), expire_on_commit=False)
    return _factories[kind]


async def dispose_engines() -> None:
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()
    _factories.clear()


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, _transaction: Any, connection: Any) -> None:
    # Runs for every new transaction, including ones started after a mid-request
    # commit, so the tenant context can never silently drop off.
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})


def on_commit(session: AsyncSession, callback: AfterCommit) -> None:
    """Schedule work (realtime publish, task enqueue) for after a successful commit."""
    session.info.setdefault("after_commit", []).append(callback)


async def _run_after_commit(session: AsyncSession) -> None:
    callbacks: list[AfterCommit] = session.info.pop("after_commit", [])
    for cb in callbacks:
        try:
            await cb()
        except Exception:  # noqa: BLE001 - side effects must never fail a committed request
            import structlog

            structlog.get_logger().exception("after_commit_callback_failed")


@asynccontextmanager
async def tenant_session(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory("app")
    async with factory() as session:
        session.info["tenant_id"] = tenant_id
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        await _run_after_commit(session)


@asynccontextmanager
async def platform_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory("platform")
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        await _run_after_commit(session)
