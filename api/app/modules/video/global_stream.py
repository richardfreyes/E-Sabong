"""System-wide video source. Tenant finances and event ownership never enter this lookup."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.database import platform_session
from app.core.errors import Forbidden
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.video.models import VideoStream


@dataclass(frozen=True)
class BroadcastConfig:
    broadcast_tenant_id: uuid.UUID | None
    global_stream_id: uuid.UUID | None


async def configuration() -> BroadcastConfig:
    async with platform_session() as db:
        provider = await db.scalar(select(Tenant).where(Tenant.is_global_stream_provider, Tenant.deleted_at.is_(None)))
        if provider is None:
            return BroadcastConfig(None, None)
        stream_id = None
        if provider.status == TenantStatus.ACTIVE:
            stream_id = await db.scalar(
                select(VideoStream.id).where(
                    VideoStream.tenant_id == provider.id,
                    VideoStream.is_default,
                )
            )
        return BroadcastConfig(provider.id, stream_id)


async def require_provider(tenant_id: uuid.UUID) -> None:
    async with platform_session() as db:
        provider = await db.scalar(
            select(Tenant.id).where(
                Tenant.id == tenant_id,
                Tenant.is_global_stream_provider,
                Tenant.deleted_at.is_(None),
                Tenant.status == TenantStatus.ACTIVE,
            )
        )
    if provider is None:
        raise Forbidden("Only the original streaming administrator can manage broadcasts", code="global_stream_provider_required")
