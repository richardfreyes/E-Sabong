"""Owner-configured shared-domain identity resolution. Never accepts a tenant selector."""

import json
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.errors import Unauthorized, Unprocessable
from app.core.security import decode_access_token, sha256_hex
from app.modules.auth.models import AuthSession, SubjectType
from app.modules.tenants.models import Tenant
from app.modules.users.models import AGENT_TYPES, AccountType, TenantUser
from app.shared.models import Base, Timestamps


class RentalSettings(Base, Timestamps):
    __tablename__ = "rental_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    shared_domain_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    broadcast_tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    global_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    global_stream_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("video_streams.id", ondelete="RESTRICT"))


async def settings() -> RentalSettings | None:
    async with platform_session() as db:
        return await db.get(RentalSettings, 1)


async def event_visibility(tenant_id: uuid.UUID):
    """Keep completed history, but show only the official arena as an active event."""
    from sqlalchemy import true

    from app.modules.events.models import Event, EventStatus

    config = await settings()
    if not config or not config.shared_domain_enabled:
        return true()
    eid = config.global_event_id
    if eid and tenant_id != config.broadcast_tenant_id:
        eid = uuid.uuid5(tenant_id, str(eid))
    return or_(Event.status == EventStatus.COMPLETED, Event.id == eid)


async def referral_owner(db, code: str):
    try:
        identity = TenantUser.id == uuid.UUID(code)
    except ValueError:
        identity = func.lower(TenantUser.username) == code.lower()
    rows = list(
        (
            await db.scalars(
                select(TenantUser)
                .join(Tenant, Tenant.id == TenantUser.tenant_id)
                .where(
                    identity,
                    TenantUser.deleted_at.is_(None),
                    Tenant.deleted_at.is_(None),
                    TenantUser.account_type.in_([*AGENT_TYPES, AccountType.ADMIN]),
                )
                .limit(2)
            )
        ).all()
    )
    if len(rows) != 1:
        raise Unprocessable("Invitation is invalid or ambiguous. Ask your inviter for a new link.", code="invalid_referrer")
    return rows[0]


async def request_body(request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Unprocessable("Request must contain a JSON object", code="invalid_request") from None
    if not isinstance(body, dict):
        raise Unprocessable("Request must contain a JSON object", code="invalid_request")
    return body


async def resolve_shared(request, config: RentalSettings, token: str | None = None):
    """Return the authenticated account's tenant, or the validated invitation's tenant."""
    from app.core.tenancy import TenantContext

    tenant_id = None
    path = request.url.path
    header = request.headers.get("authorization", "")
    token = token or (header[7:] if header.lower().startswith("bearer ") else None)
    if path.endswith(("/auth/login", "/auth/register", "/auth/referral", "/auth/refresh", "/auth/logout")):
        token = None  # Refresh uses its HttpOnly cookie, including when the bearer has expired.
    async with platform_session() as db:
        if token:
            tenant_id = decode_access_token(token, "tenant").tenant_id
        else:
            if path.endswith(("/auth/refresh", "/auth/logout")):
                from app.modules.auth import service

                portal = request.headers.get("x-portal")
                raw = request.cookies.get(service.tenant_refresh_cookie(portal))
                if raw:
                    session = await db.scalar(
                        select(AuthSession).where(
                            AuthSession.token_hash == sha256_hex(raw),
                            AuthSession.subject_type == SubjectType.TENANT,
                        )
                    )
                    if session:
                        tenant_id = session.tenant_id
                if tenant_id is None:
                    raise Unauthorized("No session", code="invalid_refresh_token")
            elif path.endswith("/auth/login"):
                from app.modules.audit.service import RequestMeta
                from app.modules.auth import service

                body = await request_body(request)
                ident = str(body.get("username", "")).strip().lower()
                await service.ensure_not_locked("shared-login", ident, RequestMeta.from_request(request).ip)
                query = select(TenantUser.tenant_id).where(
                    TenantUser.deleted_at.is_(None),
                    or_(func.lower(TenantUser.username) == ident, func.lower(TenantUser.email) == ident),
                )
                # Existing duplicate usernames remain intact. A qualified username disambiguates
                # identity only; the password and account status are still checked by login.
                if "@" in ident:
                    username, slug = ident.rsplit("@", 1)
                    qualified = (
                        select(TenantUser.tenant_id)
                        .join(Tenant)
                        .where(
                            func.lower(TenantUser.username) == username,
                            Tenant.slug == slug,
                            TenantUser.deleted_at.is_(None),
                            Tenant.deleted_at.is_(None),
                        )
                    )
                    candidates = list((await db.scalars(qualified.limit(2))).all())
                    if not candidates:
                        candidates = list((await db.scalars(query.limit(2))).all())
                    else:
                        request.state.login_username = username
                else:
                    candidates = list((await db.scalars(query.limit(2))).all())
                if len(candidates) != 1:
                    await service.register_failure("shared-login", ident, RequestMeta.from_request(request).ip)
                    raise Unauthorized(
                        "Invalid credentials. For a shared username, use username@workspace-code.", code="invalid_credentials"
                    )
                tenant_id = candidates[0]
            elif path.endswith(("/auth/register", "/auth/referral")):
                code = request.query_params.get("ref") if path.endswith("/referral") else (await request_body(request)).get("ref")
                if code:
                    tenant_id = (await referral_owner(db, str(code).strip())).tenant_id
                else:
                    tenant_id = config.default_tenant_id
                    if tenant_id is None:
                        raise Unprocessable("An invitation is required to register", code="invitation_required")
            else:
                tenant_id = config.default_tenant_id
                if tenant_id is None and path.endswith("/public/tenant"):
                    tenant_id = config.broadcast_tenant_id
                if tenant_id is None and get_settings().root_tenant_slug:
                    tenant_id = await db.scalar(
                        select(Tenant.id).where(Tenant.slug == get_settings().root_tenant_slug, Tenant.deleted_at.is_(None))
                    )
        tenant = await db.get(Tenant, tenant_id) if tenant_id else None
        if tenant is None or tenant.deleted_at is not None:
            return None
        if token or path.endswith(("/auth/login", "/auth/register", "/auth/refresh")):
            from app.core.errors import Forbidden
            from app.core.security import utcnow
            from app.modules.subscriptions.service import get_live

            sub = await get_live(db, tenant.id)
            if sub and sub.current_period_start > utcnow():
                raise Forbidden("This subscription has not activated yet", code="subscription_not_active")
        return TenantContext(tenant.id, tenant.slug, tenant.name, tenant.status)
