from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUserDep,
    Meta,
    TenantDb,
    TenantDep,
    load_permissions,
)
from app.core.errors import Forbidden, Unauthorized, Unprocessable
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    utcnow,
    validate_password_policy,
    verify_password,
)
from app.core.tenancy import TenantContext
from app.modules.audit import service as audit
from app.modules.audit.models import ActorType
from app.modules.audit.service import Actor
from app.modules.auth import service
from app.modules.auth.models import AccountStatus, SubjectType
from app.modules.auth.schemas import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RegisterRequest,
    TenantLoginResponse,
    TenantProfile,
    TenantSummary,
    TokenResponse,
)
from app.modules.tenants.models import TenantSettings
from app.modules.users import service as users_service
from app.modules.users.models import AccountType, TenantUser
from app.modules.users.schemas import UserCreate
from app.shared.schemas import Message

router = APIRouter(prefix="/auth", tags=["auth"])


async def build_profile(
    db: AsyncSession, user: TenantUser, tenant: TenantContext, perms: frozenset[str] | None = None
) -> TenantProfile:
    perms = perms if perms is not None else await load_permissions(db, tenant.id, user.id)
    roles = (await users_service.roles_for_users(db, tenant.id, [user.id])).get(user.id, [])
    return TenantProfile(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        account_type=user.account_type,
        parent_id=user.parent_id,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        permissions=sorted(perms),
        roles=[r.name for r in roles],
        tenant=TenantSummary(id=tenant.id, slug=tenant.slug, name=tenant.name),
    )


async def _complete_login(
    db: AsyncSession, response: Response, user: TenantUser, tenant: TenantContext, meta: audit.RequestMeta
) -> TenantLoginResponse:
    issued = await service.issue_session(db, subject_type=SubjectType.TENANT, subject_id=user.id, tenant_id=tenant.id, meta=meta)
    token, ttl = create_access_token(subject_id=user.id, audience="tenant", tenant_id=tenant.id, family=issued.family_id)
    service.set_refresh_cookie(response, service.TENANT_REFRESH_COOKIE, service.TENANT_COOKIE_PATH, issued.refresh_token)
    return TenantLoginResponse(access_token=token, expires_in=ttl, user=await build_profile(db, user, tenant))


@router.post("/login", response_model=TenantLoginResponse)
async def login(body: LoginRequest, response: Response, tenant: TenantDep, db: TenantDb, meta: Meta) -> TenantLoginResponse:
    scope = f"tenant:{tenant.id}"
    await service.ensure_not_locked(scope, body.username)
    ident = body.username.lower()
    user = (
        await db.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant.id,
                TenantUser.deleted_at.is_(None),
                or_(func.lower(TenantUser.username) == ident, func.lower(TenantUser.email) == ident),
            )
        )
    ).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        await service.register_failure(scope, body.username)
        if user is not None:
            await audit.record(
                db,
                actor=Actor(ActorType.TENANT_USER, user.id, user.username),
                action="auth.login_failed",
                resource_type="tenant_user",
                resource_id=user.id,
                tenant_id=tenant.id,
                meta=meta,
            )
            await db.commit()
        raise Unauthorized("Invalid username or password", code="invalid_credentials")
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("This account is suspended", code="account_suspended")

    await service.clear_failures(scope, body.username)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    await audit.record(
        db,
        actor=Actor(ActorType.TENANT_USER, user.id, user.username),
        action="auth.login",
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=tenant.id,
        meta=meta,
    )
    return await _complete_login(db, response, user, tenant, meta)


@router.post("/register", response_model=TenantLoginResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, response: Response, tenant: TenantDep, db: TenantDb, meta: Meta) -> TenantLoginResponse:
    settings = await db.get(TenantSettings, tenant.id)
    if settings is None or not settings.allow_self_registration:
        raise Forbidden("Registration is not open for this workspace", code="registration_closed")
    user, _ = await users_service.create_user(
        db,
        tenant_id=tenant.id,
        data=UserCreate(
            username=body.username,
            email=body.email,
            full_name=body.full_name,
            phone=body.phone,
            account_type=AccountType.PLAYER,
            password=body.password,
        ),
        actor=None,
        audit_actor=Actor(ActorType.SYSTEM, None, "self-registration"),
        meta=meta,
    )
    user.last_login_at = utcnow()
    return await _complete_login(db, response, user, tenant, meta)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    tenant: TenantDep,
    db: TenantDb,
    meta: Meta,
    token: Annotated[str | None, Cookie(alias=service.TENANT_REFRESH_COOKIE)] = None,
) -> TokenResponse:
    service.require_csrf_header(request)
    if not token:
        raise Unauthorized("No session", code="invalid_refresh_token")
    current, issued = await service.rotate_session(db, token, subject_type=SubjectType.TENANT, meta=meta)
    if current.tenant_id != tenant.id:
        raise Unauthorized("Session is invalid", code="invalid_refresh_token")
    user = await db.get(TenantUser, current.subject_id)
    if user is None or user.tenant_id != tenant.id or user.deleted_at is not None or user.status != AccountStatus.ACTIVE:
        await service.revoke_family(db, current.family_id, "account_inactive")
        raise Unauthorized("Account is not active", code="account_inactive")
    access, ttl = create_access_token(subject_id=user.id, audience="tenant", tenant_id=tenant.id, family=issued.family_id)
    service.set_refresh_cookie(response, service.TENANT_REFRESH_COOKIE, service.TENANT_COOKIE_PATH, issued.refresh_token)
    return TokenResponse(access_token=access, expires_in=ttl)


@router.post("/logout", response_model=Message)
async def logout(
    request: Request,
    response: Response,
    db: TenantDb,
    token: Annotated[str | None, Cookie(alias=service.TENANT_REFRESH_COOKIE)] = None,
) -> Message:
    service.require_csrf_header(request)
    await service.logout_session(db, token)
    service.clear_refresh_cookie(response, service.TENANT_REFRESH_COOKIE, service.TENANT_COOKIE_PATH)
    return Message(message="Signed out")


@router.get("/me", response_model=TenantProfile)
async def me(current: CurrentUserDep, db: TenantDb) -> TenantProfile:
    return await build_profile(db, current.user, current.tenant, current.permissions)


@router.patch("/me", response_model=TenantProfile)
async def update_me(body: ProfileUpdate, current: CurrentUserDep, db: TenantDb, meta: Meta) -> TenantProfile:
    user = current.user
    fields = body.model_dump(exclude_unset=True)
    if fields.get("email") and fields["email"].lower() != (user.email or "").lower():
        taken = (
            await db.execute(
                select(TenantUser.id).where(
                    TenantUser.tenant_id == current.tenant.id,
                    func.lower(TenantUser.email) == fields["email"].lower(),
                    TenantUser.id != user.id,
                    TenantUser.deleted_at.is_(None),
                )
            )
        ).first()
        if taken:
            raise Unprocessable("Email is already in use", code="email_taken")
    before = {k: getattr(user, k) for k in fields}
    for key, value in fields.items():
        setattr(user, key, value)
    await db.flush()
    await audit.record(
        db,
        actor=current.actor,
        action="profile.updated",
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=current.tenant.id,
        changes=audit.diff(before, {k: getattr(user, k) for k in fields}),
        meta=meta,
    )
    return await build_profile(db, user, current.tenant, current.permissions)


@router.post("/me/password", response_model=Message)
async def change_password(body: PasswordChange, current: CurrentUserDep, db: TenantDb, meta: Meta) -> Message:
    user = current.user
    if not verify_password(body.current_password, user.password_hash):
        raise Unprocessable("Current password is incorrect", code="invalid_current_password")
    if body.current_password == body.new_password:
        raise Unprocessable("Choose a different password", code="password_reused")
    validate_password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    await service.revoke_subject_sessions(db, SubjectType.TENANT, user.id, except_family=current.session_family)
    await audit.record(
        db,
        actor=current.actor,
        action="auth.password_changed",
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=current.tenant.id,
        meta=meta,
    )
    return Message(message="Password updated")
