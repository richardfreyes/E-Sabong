"""Suspend only accounts created by a matching UAT verification fixture; retain all history."""

import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import dispose_engines, platform_session, tenant_session
from app.core.deps import CurrentUser, load_permissions
from app.core.redis import close_redis
from app.core.tenancy import TenantContext
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.auth.models import AccountStatus, PlatformUser, SubjectType
from app.modules.auth.service import revoke_subject_sessions
from app.modules.tenants.models import Tenant
from app.modules.users.models import AccountType, TenantUser
from app.modules.users.service import set_status


async def main(fixture_path: str) -> None:
    fixture = json.loads(await asyncio.to_thread(Path(fixture_path).read_text))
    settings = get_settings()
    if (
        fixture["origin"] != "https://" + settings.base_domain
        or not settings.base_domain.startswith("uat.")
        or not settings.synthetic_data_enabled
    ):
        raise ValueError("Matching UAT fixture required")
    async with platform_session() as db:
        tenant = await db.get(Tenant, uuid.UUID(fixture["tenant_id"]))
        existing = await db.scalar(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == tenant.id,
                TenantUser.account_type == AccountType.ADMIN,
                TenantUser.status == AccountStatus.ACTIVE,
                TenantUser.deleted_at.is_(None),
                ~TenantUser.username.startswith("perf-"),
            )
            .order_by(TenantUser.created_at)
        )
        if existing is None:
            raise ValueError("Existing UAT administrator required")
    context = TenantContext(tenant.id, tenant.slug, tenant.name, tenant.status)
    async with tenant_session(tenant.id) as db:
        actor = CurrentUser(
            user=existing,
            permissions=await load_permissions(db, tenant.id, existing.id),
            tenant=context,
            session_family=uuid.uuid4(),
        )
        for entry in fixture["accounts"].values():
            user = await db.get(TenantUser, uuid.UUID(entry["id"]))
            if (
                not user
                or user.tenant_id != tenant.id
                or user.username != entry["username"]
                or not user.username.startswith("perf-" + fixture["run"] + "-")
            ):
                raise ValueError("Refusing to suspend an account outside this fixture")
            await set_status(
                db,
                actor,
                user.id,
                AccountStatus.SUSPENDED,
                RequestMeta(user_agent="declarator-uat-verification-cleanup"),
            )
    async with platform_session() as db:
        email = "declarator-" + fixture["run"] + "@example.com"
        if fixture["owner_email"] != email:
            raise ValueError("Mismatched fixture owner")
        owner = await db.scalar(select(PlatformUser).where(PlatformUser.email == email))
        if owner:
            owner.status = AccountStatus.SUSPENDED
            await revoke_subject_sessions(db, SubjectType.PLATFORM, owner.id)
            await audit.record(
                db,
                actor=Actor.system(),
                action="verification.owner_suspended",
                resource_type="platform_user",
                resource_id=owner.id,
                changes={"run": fixture["run"]},
                meta=RequestMeta(),
            )
    await dispose_engines()
    await close_redis()
    print(
        json.dumps(
            {
                "cleaned_up": True,
                "run": fixture["run"],
                "suspended_accounts": len(fixture["accounts"]) + 1,
                "history_retained": True,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
