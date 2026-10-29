import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import ActorType, AuditLog


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: uuid.UUID | None
    label: str | None = None

    @classmethod
    def system(cls) -> "Actor":
        return cls(ActorType.SYSTEM, None, "system")


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    @classmethod
    def from_request(cls, request: Request) -> "RequestMeta":
        return cls(
            ip=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
            request_id=getattr(request.state, "request_id", None),
        )


SENSITIVE_KEYS = {"password", "password_hash", "token", "token_hash", "key", "key_hash", "secret"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k in SENSITIVE_KEYS else _jsonable(v)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Field-level change set: {field: [old, new]} for fields that changed."""
    changes = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = [old, new]
    return changes


async def record(
    db: AsyncSession,
    *,
    actor: Actor,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
    tenant_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> None:
    """Write an audit record in the caller's transaction (atomic with the change)."""
    meta = meta or RequestMeta()
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_type=actor.type,
            actor_id=actor.id,
            actor_label=actor.label,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            changes=_jsonable(changes or {}),
            ip=meta.ip,
            user_agent=meta.user_agent,
            request_id=meta.request_id,
        )
    )
