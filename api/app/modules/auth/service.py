import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import revoked_family_key
from app.core.errors import TooManyRequests, Unauthorized
from app.core.redis import get_redis
from app.core.security import new_opaque_token, sha256_hex, utcnow
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AuthSession, SubjectType

TENANT_REFRESH_COOKIE = "esb_rt"
PLATFORM_REFRESH_COOKIE = "esb_prt"
TENANT_COOKIE_PATH = "/api/v1/auth"
PLATFORM_COOKIE_PATH = "/api/v1/platform/auth"


# ---------------------------------------------------------------- throttling


def _fail_key(scope: str, identifier: str) -> str:
    return f"auth:fail:{scope}:{sha256_hex(identifier.lower())}"


def _ip_fail_key(ip: str) -> str:
    return f"auth:ipfail:{sha256_hex(ip)}"


async def _raise_if_over(key: str, limit: int, message: str, code: str) -> None:
    redis = get_redis()
    if int(await redis.get(key) or 0) >= limit:
        ttl = await redis.ttl(key)
        raise TooManyRequests(message, code=code, headers={"Retry-After": str(max(ttl, 1))})


async def _count(key: str, window_seconds: int) -> int:
    redis = get_redis()
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, window_seconds)
    return count


async def ensure_not_locked(scope: str, identifier: str, ip: str | None = None) -> None:
    """Refuse a sign-in while the account, or the client address, has too many recent failures."""
    s = get_settings()
    await _raise_if_over(
        _fail_key(scope, identifier), s.login_max_attempts, "Too many failed sign-in attempts. Try again later.", "login_locked"
    )
    if ip:
        await _raise_if_over(
            _ip_fail_key(ip),
            s.login_ip_max_failures,
            "Too many failed sign-in attempts from this network. Try again later.",
            "login_rate_limited",
        )


async def register_failure(scope: str, identifier: str, ip: str | None = None) -> None:
    s = get_settings()
    await _count(_fail_key(scope, identifier), s.login_lockout_seconds)
    if ip:
        # Counted across every account and tenant: password spraying rotates usernames, not addresses.
        await _count(_ip_fail_key(ip), s.login_ip_window_seconds)


async def clear_failures(scope: str, identifier: str) -> None:
    # Only the account counter: one correct password must not reset an address's spraying history.
    await get_redis().delete(_fail_key(scope, identifier))


async def throttle_registration(ip: str | None) -> None:
    """Self-registration: at most REGISTER_IP_MAX_PER_HOUR new accounts per client address."""
    if not ip:
        return
    key = f"ratelimit:register:{sha256_hex(ip)}"
    if await _count(key, 3600) > get_settings().register_ip_max_per_hour:
        ttl = await get_redis().ttl(key)
        raise TooManyRequests(
            "Too many registrations from this network. Try again later.",
            code="registration_rate_limited",
            headers={"Retry-After": str(max(ttl, 1))},
        )


# ---------------------------------------------------------------- refresh sessions


@dataclass(frozen=True)
class IssuedSession:
    refresh_token: str
    family_id: uuid.UUID
    session_id: uuid.UUID


async def issue_session(
    db: AsyncSession,
    *,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    meta: RequestMeta,
    family_id: uuid.UUID | None = None,
) -> IssuedSession:
    raw = new_opaque_token(32)
    row = AuthSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        subject_type=subject_type,
        subject_id=subject_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=sha256_hex(raw),
        expires_at=utcnow() + timedelta(seconds=get_settings().refresh_token_ttl_seconds),
        ip=meta.ip,
        user_agent=meta.user_agent,
    )
    db.add(row)
    await db.flush()
    return IssuedSession(refresh_token=raw, family_id=row.family_id, session_id=row.id)


async def revoke_family(db: AsyncSession, family_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    # Access tokens of this family stop working immediately, not at expiry.
    await get_redis().set(revoked_family_key(family_id), "1", ex=get_settings().access_token_ttl_seconds + 60)


async def revoke_subject_sessions(
    db: AsyncSession, subject_type: SubjectType, subject_id: uuid.UUID, *, except_family: uuid.UUID | None = None
) -> None:
    stmt = select(AuthSession.family_id).where(
        AuthSession.subject_type == subject_type,
        AuthSession.subject_id == subject_id,
        AuthSession.revoked_at.is_(None),
    )
    if except_family is not None:
        stmt = stmt.where(AuthSession.family_id != except_family)
    families = set((await db.execute(stmt)).scalars().all())
    for family in families:
        await revoke_family(db, family, "revoked")


async def rotate_session(
    db: AsyncSession, raw_token: str, *, subject_type: SubjectType, meta: RequestMeta
) -> tuple[AuthSession, IssuedSession]:
    current = (
        await db.execute(select(AuthSession).where(AuthSession.token_hash == sha256_hex(raw_token)).with_for_update())
    ).scalar_one_or_none()
    if current is None or current.subject_type != subject_type:
        raise Unauthorized("Session is invalid", code="invalid_refresh_token")
    if current.revoked_at is not None:
        if current.revoked_reason == "rotated":
            # A rotated token was presented again: assume theft, kill the family.
            await revoke_family(db, current.family_id, "reuse_detected")
            await db.commit()
            raise Unauthorized("Session has been revoked", code="refresh_token_reused")
        raise Unauthorized("Session has been revoked", code="invalid_refresh_token")
    if current.expires_at <= utcnow():
        raise Unauthorized("Session expired", code="refresh_token_expired")

    issued = await issue_session(
        db,
        subject_type=subject_type,
        subject_id=current.subject_id,
        tenant_id=current.tenant_id,
        meta=meta,
        family_id=current.family_id,
    )
    current.revoked_at = utcnow()
    current.revoked_reason = "rotated"
    current.replaced_by_id = issued.session_id
    return current, issued


async def logout_session(db: AsyncSession, raw_token: str | None) -> None:
    if not raw_token:
        return
    row = (await db.execute(select(AuthSession).where(AuthSession.token_hash == sha256_hex(raw_token)))).scalar_one_or_none()
    if row is not None:
        await revoke_family(db, row.family_id, "logout")


# ---------------------------------------------------------------- cookies


def set_refresh_cookie(response: Response, name: str, path: str, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=name,
        value=token,
        max_age=s.refresh_token_ttl_seconds,
        path=path,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response, name: str, path: str) -> None:
    s = get_settings()
    response.delete_cookie(key=name, path=path, httponly=True, secure=s.cookie_secure, samesite="strict")


def require_csrf_header(request: Request) -> None:
    """Cookie-authenticated endpoints additionally require a custom header.

    Browsers cannot attach custom headers to cross-site form posts, so this blocks
    CSRF even where SameSite is not honoured.
    """
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise Unauthorized("Missing X-Requested-With header", code="csrf_check_failed")
