"""FastAPI dependencies for tenant resolution, database sessions, auth and RBAC."""

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import platform_session, tenant_session
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.permissions import PLATFORM_ROLE_RANK, P
from app.core.redis import get_redis
from app.core.security import decode_access_token
from app.core.tenancy import TenantContext, resolve_tenant
from app.modules.audit.models import ActorType
from app.modules.audit.service import Actor, RequestMeta
from app.modules.auth.models import AccountStatus, PlatformRole, PlatformUser
from app.modules.users.models import RolePermission, TenantUser, UserRole

bearer = HTTPBearer(auto_error=False, description="Access token from the login endpoint")


def revoked_family_key(family: uuid.UUID) -> str:
    return f"auth:revoked_family:{family}"


# ---------------------------------------------------------------- tenant + db


async def current_tenant_optional(request: Request) -> TenantContext | None:
    ctx = await resolve_tenant(request)
    if ctx is not None:
        request.state.tenant_slug = ctx.slug
        request.state.tenant_id = ctx.id
    return ctx


async def require_tenant(
    ctx: Annotated[TenantContext | None, Depends(current_tenant_optional)],
) -> TenantContext:
    if ctx is None:
        raise NotFound("No tenant is configured for this host", code="tenant_not_found")
    if not ctx.is_operational:
        raise Forbidden(
            "This workspace is currently unavailable",
            code="tenant_suspended",
            details={"status": ctx.status.value},
        )
    return ctx


async def get_tenant_db(
    tenant: Annotated[TenantContext, Depends(require_tenant)],
) -> AsyncIterator[AsyncSession]:
    async with tenant_session(tenant.id) as session:
        yield session


async def get_platform_db() -> AsyncIterator[AsyncSession]:
    async with platform_session() as session:
        yield session


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta.from_request(request)


TenantDep = Annotated[TenantContext, Depends(require_tenant)]
TenantDb = Annotated[AsyncSession, Depends(get_tenant_db)]
PlatformDb = Annotated[AsyncSession, Depends(get_platform_db)]
Meta = Annotated[RequestMeta, Depends(request_meta)]


# ---------------------------------------------------------------- tenant users


@dataclass
class CurrentUser:
    user: TenantUser
    permissions: frozenset[str]
    tenant: TenantContext
    session_family: uuid.UUID

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    def has(self, *perms: P | str) -> bool:
        return all(str(p) in self.permissions for p in perms)

    def has_any(self, *perms: P | str) -> bool:
        return any(str(p) in self.permissions for p in perms)

    @property
    def actor(self) -> Actor:
        return Actor(ActorType.TENANT_USER, self.user.id, self.user.username)


async def load_permissions(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[str]:
    rows = await db.execute(
        select(RolePermission.permission)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.tenant_id == tenant_id, UserRole.user_id == user_id)
        .distinct()
    )
    return frozenset(rows.scalars().all())


# Endpoints reachable while an account still has a temporary password.
PASSWORD_CHANGE_ALLOWED = frozenset({"/api/v1/auth/me", "/api/v1/auth/me/password", "/api/v1/auth/logout"})


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    tenant: TenantDep,
    db: TenantDb,
) -> CurrentUser:
    if creds is None:
        raise Unauthorized("Authentication required", code="not_authenticated")
    claims = decode_access_token(creds.credentials, "tenant")
    # A token minted for one tenant is never valid on another tenant's host.
    if claims.tenant_id != tenant.id:
        raise Unauthorized("Token is not valid for this workspace", code="invalid_token")
    if await get_redis().exists(revoked_family_key(claims.session_family)):
        raise Unauthorized("Session has been revoked", code="session_revoked")
    user = await db.get(TenantUser, claims.subject_id)
    if user is None or user.tenant_id != tenant.id or user.deleted_at is not None or user.status != AccountStatus.ACTIVE:
        raise Unauthorized("Account is not active", code="account_inactive")
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED:
        raise Forbidden("Change your temporary password to continue", code="password_change_required")
    perms = await load_permissions(db, tenant.id, user.id)
    return CurrentUser(user=user, permissions=perms, tenant=tenant, session_family=claims.session_family)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_permissions(
    *perms: P,
) -> Callable[[CurrentUser], Coroutine[Any, Any, CurrentUser]]:
    async def _check(user: CurrentUserDep) -> CurrentUser:
        if not user.has(*perms):
            raise Forbidden(
                "You do not have permission to perform this action",
                code="permission_denied",
                details={"required": [str(p) for p in perms]},
            )
        return user

    return _check


def require_any_permission(
    *perms: P,
) -> Callable[[CurrentUser], Coroutine[Any, Any, CurrentUser]]:
    async def _check(user: CurrentUserDep) -> CurrentUser:
        if not user.has_any(*perms):
            raise Forbidden(
                "You do not have permission to perform this action",
                code="permission_denied",
                details={"required_any": [str(p) for p in perms]},
            )
        return user

    return _check


def require_module(module: str) -> Callable[..., Coroutine[Any, Any, None]]:
    async def _check(tenant: TenantDep) -> None:
        from app.modules.tenants.service import effective_modules

        if module not in await effective_modules(tenant.id):
            raise Forbidden(
                f"The '{module}' module is not enabled for this workspace",
                code="module_disabled",
                details={"module": module},
            )

    return _check


# ---------------------------------------------------------------- platform users


@dataclass
class CurrentPlatformUser:
    user: PlatformUser
    session_family: uuid.UUID

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def actor(self) -> Actor:
        return Actor(ActorType.PLATFORM_USER, self.user.id, self.user.email)


async def get_current_platform_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: PlatformDb,
) -> CurrentPlatformUser:
    if creds is None:
        raise Unauthorized("Authentication required", code="not_authenticated")
    claims = decode_access_token(creds.credentials, "platform")
    if await get_redis().exists(revoked_family_key(claims.session_family)):
        raise Unauthorized("Session has been revoked", code="session_revoked")
    user = await db.get(PlatformUser, claims.subject_id)
    if user is None or user.status != AccountStatus.ACTIVE:
        raise Unauthorized("Account is not active", code="account_inactive")
    return CurrentPlatformUser(user=user, session_family=claims.session_family)


CurrentPlatformUserDep = Annotated[CurrentPlatformUser, Depends(get_current_platform_user)]


def require_platform_role(
    minimum: PlatformRole,
) -> Callable[[CurrentPlatformUser], Coroutine[Any, Any, CurrentPlatformUser]]:
    async def _check(user: CurrentPlatformUserDep) -> CurrentPlatformUser:
        if PLATFORM_ROLE_RANK[user.user.role] < PLATFORM_ROLE_RANK[minimum]:
            raise Forbidden(
                "Your platform role does not allow this action",
                code="permission_denied",
                details={"required_role": minimum.value},
            )
        return user

    return _check


PlatformReader = Annotated[CurrentPlatformUser, Depends(require_platform_role(PlatformRole.SUPPORT))]
PlatformBilling = Annotated[CurrentPlatformUser, Depends(require_platform_role(PlatformRole.BILLING))]
PlatformAdmin = Annotated[CurrentPlatformUser, Depends(require_platform_role(PlatformRole.ADMIN))]
PlatformSuperAdmin = Annotated[CurrentPlatformUser, Depends(require_platform_role(PlatformRole.SUPER_ADMIN))]
