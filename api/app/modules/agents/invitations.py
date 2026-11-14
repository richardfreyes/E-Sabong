"""Invitation visibility is the creating account, never its parent or downline.

Every read and write filters on the authenticated user id and tenant id. A master agent
does not see invitations recorded for an agent, and an agent does not see invitations
recorded for a sub-agent, even when those accounts sit in the caller's network.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.modules.agents.models import AccountInvitation, InvitationRole, InvitationStatus
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.realtime.publisher import publish_after_commit
from app.modules.users.models import AGENT_TYPES, AccountType, TenantUser
from app.shared.schemas import Page, PageParams, Schema

StatusFilter = Literal["pending", "accepted", "revoked"]

ALLOWED_INVITES: dict[AccountType, frozenset[InvitationRole]] = {
    AccountType.MASTER_AGENT: frozenset({InvitationRole.AGENT, InvitationRole.PLAYER}),
    AccountType.AGENT: frozenset({InvitationRole.SUB_AGENT, InvitationRole.PLAYER}),
    AccountType.SUB_AGENT: frozenset({InvitationRole.PLAYER}),
}

# Existing accounts keep their parent, commission, and creator. This only records who invited
# them: the agent who created the account, or, for a referral sign-up, the referring account.
BACKFILL_SQL = """
INSERT INTO account_invitations (
    id, tenant_id, invited_by_user_id, invitee_user_id, invitee_username, invitee_full_name,
    account_type, status, created_at, updated_at
)
SELECT gen_random_uuid(), u.tenant_id, inviter.id, u.id, u.username, u.full_name,
       u.account_type, 'accepted', u.created_at, u.created_at
FROM tenant_users u
JOIN tenant_users inviter
  ON inviter.tenant_id = u.tenant_id
 AND inviter.deleted_at IS NULL
 AND inviter.account_type IN ('master_agent', 'agent', 'sub_agent')
 AND inviter.id = (
    CASE
      WHEN u.created_by_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM tenant_users creator
        WHERE creator.tenant_id = u.tenant_id
          AND creator.id = u.created_by_id
          AND creator.deleted_at IS NULL
          AND creator.account_type IN ('master_agent', 'agent', 'sub_agent')
      ) THEN u.created_by_id
      WHEN u.created_by_id IS NULL THEN u.parent_id
      ELSE NULL
    END
 )
WHERE u.deleted_at IS NULL
  AND u.account_type IN ('agent', 'sub_agent', 'player')
  AND NOT EXISTS (
    SELECT 1 FROM account_invitations existing
    WHERE existing.tenant_id = u.tenant_id AND existing.invitee_user_id = u.id
  )
"""


class InvitationOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID
    invited_by_user_id: uuid.UUID
    invitee_user_id: uuid.UUID | None
    invitee_username: str | None
    invitee_full_name: str | None
    account_type: str
    status: str
    created_at: datetime


class InvitationSummary(Schema):
    pending: int
    accepted: int
    revoked: int
    history: int


class InvitationCreate(Schema):
    account_type: Literal["agent", "sub_agent", "player"]
    invitee_username: str | None = None
    invitee_full_name: str | None = None


class InvitationUpdate(Schema):
    status: Literal["revoked"]


def _require_agent(actor: CurrentUser) -> None:
    if actor.user.account_type not in AGENT_TYPES:
        raise Forbidden("Invitations are for master agents, agents, and sub-agents", code="agents_only")


def _owned():
    invitee = aliased(TenantUser)
    stmt = select(AccountInvitation, invitee.username, invitee.full_name).outerjoin(
        invitee,
        (invitee.tenant_id == AccountInvitation.tenant_id) & (invitee.id == AccountInvitation.invitee_user_id),
    )
    return stmt, invitee


def _scoped(actor: CurrentUser, *, status: StatusFilter | None, q: str | None):
    """Rows this account created in its own tenant. Caller-supplied owner ids are not consulted."""
    stmt, invitee = _owned()
    stmt = stmt.where(
        AccountInvitation.tenant_id == actor.tenant.id,
        AccountInvitation.invited_by_user_id == actor.id,
    )
    if status:
        stmt = stmt.where(AccountInvitation.status == InvitationStatus(status))
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                AccountInvitation.invitee_username.ilike(term),
                AccountInvitation.invitee_full_name.ilike(term),
                invitee.username.ilike(term),
                invitee.full_name.ilike(term),
            )
        )
    return stmt


def _out(row: AccountInvitation, username: str | None, full_name: str | None) -> InvitationOut:
    return InvitationOut(
        id=row.id,
        tenant_id=row.tenant_id,
        invited_by_user_id=row.invited_by_user_id,
        invitee_user_id=row.invitee_user_id,
        invitee_username=username or row.invitee_username,
        invitee_full_name=full_name or row.invitee_full_name,
        account_type=row.account_type.value,
        status=row.status.value,
        created_at=row.created_at,
    )


async def backfill_invitations(db: AsyncSession) -> None:
    """Record accepted invitations for accounts that already exist. Safe to run more than once."""
    await db.execute(text(BACKFILL_SQL))


async def record_accepted(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: CurrentUser | None,
    parent_id: uuid.UUID | None,
    invitee: TenantUser,
) -> None:
    """Attach a new account to the person who actually invited it.

    Staff outside the agent hierarchy do not create an agent invitation, and the parent
    account is used only when the invitee registered themselves through that account's link.
    """
    if invitee.account_type not in (AccountType.AGENT, AccountType.SUB_AGENT, AccountType.PLAYER):
        return
    inviter_id: uuid.UUID | None = None
    if actor is not None and actor.user.account_type in AGENT_TYPES:
        inviter_id = actor.id
    elif actor is None and parent_id is not None:
        parent_type = (
            await db.execute(
                select(TenantUser.account_type).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.id == parent_id,
                    TenantUser.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if parent_type in AGENT_TYPES:
            inviter_id = parent_id
    if inviter_id is None:
        return
    existing = (
        await db.execute(
            select(AccountInvitation.id).where(
                AccountInvitation.tenant_id == tenant_id,
                AccountInvitation.invitee_user_id == invitee.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    db.add(
        AccountInvitation(
            tenant_id=tenant_id,
            invited_by_user_id=inviter_id,
            invitee_user_id=invitee.id,
            invitee_username=invitee.username,
            invitee_full_name=invitee.full_name,
            account_type=InvitationRole(invitee.account_type.value),
            status=InvitationStatus.ACCEPTED,
        )
    )
    await db.flush()


async def summary(db: AsyncSession, actor: CurrentUser) -> InvitationSummary:
    _require_agent(actor)
    rows = (
        await db.execute(
            select(AccountInvitation.status, func.count())
            .where(
                AccountInvitation.tenant_id == actor.tenant.id,
                AccountInvitation.invited_by_user_id == actor.id,
            )
            .group_by(AccountInvitation.status)
        )
    ).all()
    counts = {InvitationStatus.PENDING: 0, InvitationStatus.ACCEPTED: 0, InvitationStatus.REVOKED: 0}
    for status, count in rows:
        key = status if isinstance(status, InvitationStatus) else InvitationStatus(str(status))
        counts[key] = int(count)
    pending = counts[InvitationStatus.PENDING]
    accepted = counts[InvitationStatus.ACCEPTED]
    revoked = counts[InvitationStatus.REVOKED]
    return InvitationSummary(pending=pending, accepted=accepted, revoked=revoked, history=pending + accepted + revoked)


async def list_invitations(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    status: StatusFilter | None,
) -> Page[InvitationOut]:
    _require_agent(actor)
    stmt = _scoped(actor, status=status, q=params.q)
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(AccountInvitation.created_at.desc(), AccountInvitation.id)
            .offset(params.offset)
            .limit(params.page_size)
        )
    ).all()
    return Page(
        items=[_out(row, username, full_name) for row, username, full_name in rows],
        total=int(total),
        page=params.page,
        page_size=params.page_size,
    )


async def get_invitation(db: AsyncSession, actor: CurrentUser, invitation_id: uuid.UUID) -> InvitationOut:
    _require_agent(actor)
    found = (await db.execute(_scoped(actor, status=None, q=None).where(AccountInvitation.id == invitation_id))).first()
    if found is None:
        raise NotFound("Invitation not found")
    return _out(*found)


async def create_invitation(
    db: AsyncSession, actor: CurrentUser, data: InvitationCreate, meta: RequestMeta | None = None
) -> InvitationOut:
    _require_agent(actor)
    role = InvitationRole(data.account_type)
    if role not in ALLOWED_INVITES.get(actor.user.account_type, frozenset()):
        raise Forbidden("You cannot invite this account type", code="permission_denied")
    username = (data.invitee_username or "").strip() or None
    full_name = (data.invitee_full_name or "").strip() or None
    if username and len(username) > 64:
        raise Unprocessable("Invitee name is too long", code="invalid_invitation")
    if full_name and len(full_name) > 200:
        raise Unprocessable("Invitee name is too long", code="invalid_invitation")
    row = AccountInvitation(
        tenant_id=actor.tenant.id,
        invited_by_user_id=actor.id,
        invitee_username=username,
        invitee_full_name=full_name,
        account_type=role,
        status=InvitationStatus.PENDING,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="invitation.created",
        resource_type="account_invitation",
        resource_id=row.id,
        tenant_id=actor.tenant.id,
        changes={"account_type": role.value, "invitee_username": username},
        meta=meta,
    )
    publish_after_commit(db, actor.tenant.id, "users.updated", {})
    return _out(row, None, None)


async def revoke_invitation(
    db: AsyncSession, actor: CurrentUser, invitation_id: uuid.UUID, meta: RequestMeta | None = None
) -> InvitationOut:
    """Revoke a pending invitation. Does not change any account, parent, or commission."""
    _require_agent(actor)
    row = (
        await db.execute(
            select(AccountInvitation).where(
                AccountInvitation.tenant_id == actor.tenant.id,
                AccountInvitation.invited_by_user_id == actor.id,
                AccountInvitation.id == invitation_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Invitation not found")
    if row.status != InvitationStatus.PENDING:
        raise Conflict("Only a pending invitation can be revoked", code="invitation_not_pending")
    row.status = InvitationStatus.REVOKED
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="invitation.revoked",
        resource_type="account_invitation",
        resource_id=row.id,
        tenant_id=actor.tenant.id,
        changes={"status": ["pending", "revoked"]},
        meta=meta,
    )
    publish_after_commit(db, actor.tenant.id, "users.updated", {})
    return _out(row, None, None)


async def export_rows(
    db: AsyncSession, actor: CurrentUser, *, status: StatusFilter | None, q: str | None
) -> list[dict[str, str]]:
    _require_agent(actor)
    stmt = _scoped(actor, status=status, q=q).order_by(AccountInvitation.created_at.desc(), AccountInvitation.id).limit(10_001)
    rows = (await db.execute(stmt)).all()
    if len(rows) > 10_000:
        raise Unprocessable("Export exceeds 10,000 records. Narrow the search or status.", code="export_too_large")
    exported = []
    for row, username, full_name in rows:
        item = _out(row, username, full_name)
        exported.append(
            {
                "id": str(item.id),
                "tenant_id": str(item.tenant_id),
                "invited_by_user_id": str(item.invited_by_user_id),
                "invitee_user_id": "" if item.invitee_user_id is None else str(item.invitee_user_id),
                "invitee_username": item.invitee_username or "",
                "invitee_full_name": item.invitee_full_name or "",
                "account_type": item.account_type,
                "status": item.status,
                "created_at": item.created_at.isoformat(),
            }
        )
    return exported
