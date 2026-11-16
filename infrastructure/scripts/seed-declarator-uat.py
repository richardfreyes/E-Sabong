"""Create audited synthetic UAT accounts without changing existing users or settings.

Run inside UAT's API container after its release gates pass. The credential file
is owner-only, contains no existing account password, and must never be committed.
"""

import asyncio
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.database import dispose_engines, platform_session, tenant_session
from app.core.deps import CurrentUser, load_permissions
from app.core.redis import close_redis
from app.core.security import hash_password
from app.core.tenancy import TenantContext
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.auth.models import AccountStatus, PlatformRole, PlatformUser
from app.modules.tenants.models import Tenant
from app.modules.users.models import AccountType, TenantUser
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user


async def main(output: str, domain: str) -> None:
    settings = get_settings()
    if settings.base_domain != domain or not domain.startswith("uat.") or not settings.synthetic_data_enabled:
        raise ValueError("Explicit matching UAT domain and synthetic-data flag required")
    destination = Path(output)
    if await asyncio.to_thread(destination.exists):
        raise ValueError("Choose a new private fixture file")
    run = uuid.uuid4().hex[:10]
    password = "Uat!" + secrets.token_hex(20)
    async with platform_session() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == settings.root_tenant_slug, Tenant.deleted_at.is_(None)))
        if not tenant:
            raise ValueError("UAT root workspace required")
        operator = await db.scalar(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == tenant.id,
                TenantUser.account_type == AccountType.ADMIN,
                TenantUser.status == AccountStatus.ACTIVE,
                TenantUser.deleted_at.is_(None),
            )
            .order_by(TenantUser.created_at)
        )
        owner = PlatformUser(
            email=f"declarator-{run}@example.com",
            full_name="Synthetic Declarator verification",
            role=PlatformRole.SUPER_ADMIN,
            password_hash=hash_password(password),
        )
        db.add(owner)
        await db.flush()
        owner_email = owner.email
        await audit.record(
            db,
            actor=Actor.system(),
            action="verification.owner_created",
            resource_type="platform_user",
            resource_id=owner.id,
            changes={"run": run, "purpose": "synthetic UAT release verification"},
            meta=RequestMeta(),
        )
    context = TenantContext(tenant.id, tenant.slug, tenant.name, tenant.status)
    protected = {}
    async with platform_session() as db:
        for table in (
            "play_wallets",
            "play_wallet_entries",
            "bets",
            "fight_books",
            "commission_entries",
            "audit_logs",
            "match_results",
            "match_result_history",
            "result_declarations",
            "dummy_stake_receipts",
        ):
            if await db.scalar(text("SELECT to_regclass(:name)"), {"name": table}):
                protected[table] = dict(
                    (
                        await db.execute(
                            text(
                                f"SELECT id::text,md5(to_jsonb(t)::text) FROM {table} t"  # noqa: S608 - constant table allowlist
                            )
                        )
                    ).all()
                )
    accounts = {}
    async with tenant_session(tenant.id) as db:
        actor = CurrentUser(
            user=operator,
            permissions=await load_permissions(db, tenant.id, operator.id),
            tenant=context,
            session_family=uuid.uuid4(),
        )
        for name, kind in [
            ("admin", AccountType.ADMIN),
            ("judge", AccountType.DECLARATOR),
            *[(f"player{i}", AccountType.PLAYER) for i in range(1, 5)],
        ]:
            user, _ = await create_user(
                db,
                tenant_id=tenant.id,
                data=UserCreate(
                    username=f"perf-{run}-{name}",
                    full_name=f"Synthetic {name}",
                    account_type=kind,
                    password=password,
                ),
                actor=actor,
                audit_actor=Actor.system(),
                meta=RequestMeta(user_agent="declarator-uat-verification"),
            )
            accounts[name] = {
                "id": str(user.id),
                "username": user.username,
                "login": user.username + "@" + tenant.slug,
            }
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(
            {
                "run": run,
                "origin": "https://" + domain,
                "tenant_id": str(tenant.id),
                "tenant_slug": tenant.slug,
                "password": password,
                "owner_email": owner_email,
                "accounts": accounts,
                "protected": protected,
            },
            file,
        )
    print(
        json.dumps(
            {
                "fixture_created": True,
                "run": run,
                "accounts": len(accounts),
                "credentials_file": str(destination),
            }
        )
    )
    await dispose_engines()
    await close_redis()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
