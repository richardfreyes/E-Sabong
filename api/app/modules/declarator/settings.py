"""Tenant-owned quick buttons; writes share the existing settings transaction and RLS."""

from typing import Annotated

from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.core.permissions import P
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.betting.schemas import Amount
from app.modules.realtime.publisher import publish_after_commit
from app.modules.tenants.models import TenantSettings
from app.modules.users.models import AccountType
from app.shared.schemas import Schema


class QuickBetSettings(Schema):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    meron: Annotated[list[Amount], Field(min_length=4, max_length=4)] = Field(default_factory=lambda: [5000, 10000, 20000, 50000])
    wala: Annotated[list[Amount], Field(min_length=4, max_length=4)] = Field(default_factory=lambda: [5000, 10000, 20000, 50000])


def authorize(actor: CurrentUser, *, write: bool = False) -> None:
    admin = actor.user.account_type == AccountType.ADMIN and actor.has(P.SETTINGS_MANAGE)
    declarator = actor.user.account_type == AccountType.DECLARATOR and actor.has(P.RESULTS_SUBMIT)
    if not (admin or (declarator and not write)):
        raise Forbidden("Only administrators may configure Declarator quick bets")


async def read(db: AsyncSession, actor: CurrentUser) -> QuickBetSettings:
    authorize(actor)
    row = await db.get(TenantSettings, actor.tenant.id)
    return QuickBetSettings.model_validate(row.declarator_quick_bets or {}) if row else QuickBetSettings()


async def save(db: AsyncSession, actor: CurrentUser, data: QuickBetSettings, meta: RequestMeta) -> QuickBetSettings:
    authorize(actor, write=True)
    row = (
        await db.execute(select(TenantSettings).where(TenantSettings.tenant_id == actor.tenant.id).with_for_update())
    ).scalar_one()
    row.declarator_quick_bets = data.model_dump(mode="json")
    await audit.record(
        db,
        actor=actor.actor,
        tenant_id=actor.tenant.id,
        action="declarator.settings_updated",
        resource_type="tenant_settings",
        resource_id=actor.tenant.id,
        changes=row.declarator_quick_bets,
        meta=meta,
    )
    await db.flush()
    publish_after_commit(db, actor.tenant.id, "declarator.settings_updated", {})
    return data
