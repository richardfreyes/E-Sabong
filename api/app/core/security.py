import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import Unauthorized

Audience = Literal["platform", "tenant"]

_hasher = PasswordHasher()  # Argon2id with library defaults (RFC 9106 low-memory profile)
# Verified against when the account does not exist, to keep timing uniform.
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AccessClaims:
    subject_id: uuid.UUID
    audience: Audience
    tenant_id: uuid.UUID | None
    session_family: uuid.UUID
    expires_at: datetime


def create_access_token(
    *, subject_id: uuid.UUID, audience: Audience, tenant_id: uuid.UUID | None, family: uuid.UUID
) -> tuple[str, int]:
    s = get_settings()
    now = utcnow()
    ttl = s.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": str(subject_id),
        "aud": audience,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
        "sfam": str(family),
        "typ": "access",
    }
    if tenant_id is not None:
        payload["tid"] = str(tenant_id)
    token = jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)
    return token, ttl


def decode_access_token(token: str, audience: Audience) -> AccessClaims:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            audience=audience,
            options={"require": ["exp", "sub", "aud", "sfam"]},
            leeway=5,
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token", code="invalid_token") from exc
    if payload.get("typ") != "access":
        raise Unauthorized("Invalid access token", code="invalid_token")
    try:
        return AccessClaims(
            subject_id=uuid.UUID(payload["sub"]),
            audience=audience,
            tenant_id=uuid.UUID(payload["tid"]) if payload.get("tid") else None,
            session_family=uuid.UUID(payload["sfam"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (ValueError, KeyError) as exc:
        raise Unauthorized("Invalid access token", code="invalid_token") from exc


def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_temporary_password() -> str:
    # 16 chars from an unambiguous alphabet; satisfies the password policy.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(14))
    return f"{body}{secrets.choice('23456789')}!"


def validate_password_policy(password: str) -> None:
    from app.core.errors import Unprocessable

    if len(password) < 10:
        raise Unprocessable("Password must be at least 10 characters", code="weak_password")
    if len(password) > 128:
        raise Unprocessable("Password must be at most 128 characters", code="weak_password")
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        raise Unprocessable(
            "Password must mix at least three of: lowercase, uppercase, digits, symbols",
            code="weak_password",
        )
