"""Agent workspace screens (``/agent``): My Users and invitations for master agents, agents, and sub-agents."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.deps import CurrentUserDep, Meta, TenantDb, require_module
from app.core.errors import Forbidden
from app.modules.agents import invitations, users
from app.modules.agents.invitations import InvitationCreate, InvitationOut, InvitationSummary, InvitationUpdate
from app.modules.agents.users import MyUserRow, MyUsersSummary
from app.modules.tenants.service import effective_flags
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/agent", tags=["agent workspace"], dependencies=[Depends(require_module("events"))])


@router.get("/users/summary", response_model=MyUsersSummary)
async def my_users_summary(current: CurrentUserDep, db: TenantDb) -> MyUsersSummary:
    """Counts of the caller's players (and, for a master agent, agents) by status, and online now."""
    return await users.summary(db, current)


@router.get("/users", response_model=Page[MyUserRow])
async def my_users(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    kind: Literal["player", "agent", "sub_agent"] | None = None,
    state: Literal["active", "blocked", "banned", "online"] | None = None,
    parent_id: uuid.UUID | None = None,
) -> Page[MyUserRow]:
    """The caller's downline players and agents, newest first."""
    return await users.list_users(db, current, params, kind=kind, state=state, parent_id=parent_id)


@router.get("/activity")
async def player_activity(current: CurrentUserDep, db: TenantDb, params: PageDep, player_id: str | None = None):
    import uuid

    from sqlalchemy import func, select

    from app.core.errors import NotFound
    from app.modules.agents.service import visible_ids_select
    from app.modules.betting.models import Bet
    from app.modules.matches.models import Match
    from app.modules.users.models import TenantUser

    users._require_agent(current)
    scope = visible_ids_select(current.tenant.id, current.id, current.user.account_type)
    stmt = (
        select(Bet, Match.match_number, TenantUser.username)
        .join(Match, (Match.tenant_id == Bet.tenant_id) & (Match.id == Bet.match_id))
        .join(TenantUser, (TenantUser.tenant_id == Bet.tenant_id) & (TenantUser.id == Bet.user_id))
        .where(Bet.tenant_id == current.tenant.id, Bet.user_id.in_(scope))
    )
    if player_id:
        try:
            uid = uuid.UUID(player_id)
        except ValueError:
            raise NotFound("Player not found") from None
        if uid not in set((await db.scalars(scope)).all()):
            raise NotFound("Player not found")
        stmt = stmt.where(Bet.user_id == uid)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    data = (await db.execute(stmt.order_by(Bet.created_at.desc(), Bet.id).offset(params.offset).limit(params.page_size))).all()
    return Page(
        items=[
            dict(
                id=b.id,
                player_id=b.user_id,
                username=name,
                match_number=number,
                amount=str(b.amount),
                side=b.side.value,
                status=b.status.value,
                payout=str(b.payout),
                created_at=b.created_at,
            )
            for b, number, name in data
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


async def _csv_allowed(db: TenantDb, tenant_id: uuid.UUID) -> None:
    if not (await effective_flags(db, tenant_id)).get("csv_exports", True):
        raise Forbidden("CSV export is not enabled for this workspace", code="feature_disabled", details={"flag": "csv_exports"})


@router.get("/invitations/summary", response_model=InvitationSummary)
async def invitation_summary(current: CurrentUserDep, db: TenantDb) -> InvitationSummary:
    """Pending, accepted, and history counts for invitations this account created."""
    return await invitations.summary(db, current)


@router.get("/invitations/export")
async def invitation_export(
    current: CurrentUserDep,
    db: TenantDb,
    q: str | None = None,
    status: Literal["pending", "accepted", "revoked"] | None = None,
) -> Response:
    """CSV of the caller's own invitations. Another account's id cannot widen the export."""
    await _csv_allowed(db, current.tenant.id)
    from app.modules.reports.service import to_csv

    rows = await invitations.export_rows(db, current, status=status, q=q)
    columns = [
        "id",
        "tenant_id",
        "invited_by_user_id",
        "invitee_user_id",
        "invitee_username",
        "invitee_full_name",
        "account_type",
        "status",
        "created_at",
    ]
    return Response(
        content=to_csv(rows, columns),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="invitations.csv"'},
    )


@router.get("/invitations", response_model=Page[InvitationOut])
async def invitation_list(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    status: Literal["pending", "accepted", "revoked"] | None = None,
) -> Page[InvitationOut]:
    """Invitations created by the authenticated account. Downline invitations are excluded."""
    return await invitations.list_invitations(db, current, params, status=status)


@router.post("/invitations", response_model=InvitationOut, status_code=201)
async def invitation_create(body: InvitationCreate, current: CurrentUserDep, db: TenantDb, meta: Meta) -> InvitationOut:
    return await invitations.create_invitation(db, current, body, meta)


@router.get("/invitations/{invitation_id}", response_model=InvitationOut)
async def invitation_detail(invitation_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> InvitationOut:
    return await invitations.get_invitation(db, current, invitation_id)


@router.patch("/invitations/{invitation_id}", response_model=InvitationOut)
async def invitation_update(
    invitation_id: uuid.UUID, body: InvitationUpdate, current: CurrentUserDep, db: TenantDb, meta: Meta
) -> InvitationOut:
    if body.status != "revoked":
        raise Forbidden("This invitation cannot be updated", code="permission_denied")
    return await invitations.revoke_invitation(db, current, invitation_id, meta)
