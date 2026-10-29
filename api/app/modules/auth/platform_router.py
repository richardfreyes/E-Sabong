from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response
from sqlalchemy import func, select

from app.core.deps import CurrentPlatformUserDep, Meta, PlatformDb
from app.core.errors import Forbidden, Unauthorized, Unprocessable
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    utcnow,
    validate_password_policy,
    verify_password,
)
from app.modules.audit import service as audit
from app.modules.audit.models import ActorType
from app.modules.audit.service import Actor
from app.modules.auth import service
from app.modules.auth.models import AccountStatus, PlatformUser, SubjectType
from app.modules.auth.schemas import (
    PasswordChange,
    PlatformLoginRequest,
    PlatformLoginResponse,
    PlatformProfile,
    TokenResponse,
)
from app.shared.schemas import Message

router = APIRouter(prefix="/platform/auth", tags=["platform: auth"])
SCOPE = "platform"


@router.post("/login", response_model=PlatformLoginResponse)
async def login(body: PlatformLoginRequest, response: Response, db: PlatformDb, meta: Meta) -> PlatformLoginResponse:
    await service.ensure_not_locked(SCOPE, body.email)
    user = (
        await db.execute(select(PlatformUser).where(func.lower(PlatformUser.email) == body.email.lower()))
    ).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        await service.register_failure(SCOPE, body.email)
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("This account is suspended", code="account_suspended")
    await service.clear_failures(SCOPE, body.email)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    issued = await service.issue_session(db, subject_type=SubjectType.PLATFORM, subject_id=user.id, tenant_id=None, meta=meta)
    token, ttl = create_access_token(subject_id=user.id, audience="platform", tenant_id=None, family=issued.family_id)
    service.set_refresh_cookie(response, service.PLATFORM_REFRESH_COOKIE, service.PLATFORM_COOKIE_PATH, issued.refresh_token)
    await audit.record(
        db,
        actor=Actor(ActorType.PLATFORM_USER, user.id, user.email),
        action="auth.login",
        resource_type="platform_user",
        resource_id=user.id,
        meta=meta,
    )
    return PlatformLoginResponse(access_token=token, expires_in=ttl, user=PlatformProfile.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: PlatformDb,
    meta: Meta,
    token: Annotated[str | None, Cookie(alias=service.PLATFORM_REFRESH_COOKIE)] = None,
) -> TokenResponse:
    service.require_csrf_header(request)
    if not token:
        raise Unauthorized("No session", code="invalid_refresh_token")
    current, issued = await service.rotate_session(db, token, subject_type=SubjectType.PLATFORM, meta=meta)
    user = await db.get(PlatformUser, current.subject_id)
    if user is None or user.status != AccountStatus.ACTIVE:
        await service.revoke_family(db, current.family_id, "account_inactive")
        raise Unauthorized("Account is not active", code="account_inactive")
    access, ttl = create_access_token(subject_id=user.id, audience="platform", tenant_id=None, family=issued.family_id)
    service.set_refresh_cookie(response, service.PLATFORM_REFRESH_COOKIE, service.PLATFORM_COOKIE_PATH, issued.refresh_token)
    return TokenResponse(access_token=access, expires_in=ttl)


@router.post("/logout", response_model=Message)
async def logout(
    request: Request,
    response: Response,
    db: PlatformDb,
    token: Annotated[str | None, Cookie(alias=service.PLATFORM_REFRESH_COOKIE)] = None,
) -> Message:
    service.require_csrf_header(request)
    await service.logout_session(db, token)
    service.clear_refresh_cookie(response, service.PLATFORM_REFRESH_COOKIE, service.PLATFORM_COOKIE_PATH)
    return Message(message="Signed out")


@router.get("/me", response_model=PlatformProfile)
async def me(current: CurrentPlatformUserDep) -> PlatformProfile:
    return PlatformProfile.model_validate(current.user)


@router.post("/me/password", response_model=Message)
async def change_password(body: PasswordChange, current: CurrentPlatformUserDep, db: PlatformDb, meta: Meta) -> Message:
    user = await db.get(PlatformUser, current.id)
    assert user is not None
    if not verify_password(body.current_password, user.password_hash):
        raise Unprocessable("Current password is incorrect", code="invalid_current_password")
    validate_password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    await service.revoke_subject_sessions(db, SubjectType.PLATFORM, user.id, except_family=current.session_family)
    await audit.record(
        db,
        actor=current.actor,
        action="auth.password_changed",
        resource_type="platform_user",
        resource_id=user.id,
        meta=meta,
    )
    return Message(message="Password updated")
