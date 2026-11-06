import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, Meta, TenantDb, require_any_permission, require_module, require_permissions
from app.core.permissions import P
from app.modules.betting.schemas import BookOut, DummyStakeIn, OddsUpdate
from app.modules.declarator import desk, service
from app.modules.declarator.models import DeclarationOutcome, DeclarationStatus
from app.modules.declarator.schemas import (
    AssignmentOut,
    BettingControl,
    DeclarationCreate,
    DeclarationOut,
    EligibleDeclarator,
    FightDetails,
    FightDetailsUpdate,
    GhostAutoOut,
    GhostAutoStart,
    JumpToFight,
    MatchHistory,
    MatchRecord,
    MonitorOut,
    OnlinePlayers,
    ReviewRequest,
    TeamNames,
)
from app.modules.events import service as events_service
from app.modules.events.schemas import EventOut, MatchOut
from app.modules.matches import service as matches
from app.shared.schemas import Message, Page, PageDep

router = APIRouter(
    prefix="/declarator",
    tags=["declarator"],
    dependencies=[
        Depends(require_module("events")),
        Depends(require_module("matches")),
    ],
)
Viewer = Annotated[CurrentUser, Depends(require_any_permission(P.RESULTS_SUBMIT, P.RESULTS_APPROVE))]
Submitter = Annotated[CurrentUser, Depends(require_permissions(P.RESULTS_SUBMIT))]
Approver = Annotated[CurrentUser, Depends(require_permissions(P.RESULTS_APPROVE))]
Manager = Annotated[CurrentUser, Depends(require_permissions(P.EVENTS_MANAGE))]


@router.get("/events", response_model=list[EventOut])
async def events(current: Viewer, db: TenantDb):  # type: ignore[no-untyped-def]
    return await service.assigned_events(db, current)


@router.get("/events/{event_id}/monitor", response_model=MonitorOut)
async def monitor(event_id: uuid.UUID, current: Viewer, db: TenantDb) -> MonitorOut:
    return await service.monitor(db, current, event_id)


@router.get("/records", response_model=Page[MatchRecord])
async def records(
    current: Viewer,
    db: TenantDb,
    params: PageDep,
    event_id: uuid.UUID | None = None,
    winner: DeclarationOutcome | None = None,
    status: DeclarationStatus | None = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> Page[MatchRecord]:
    return await service.records(
        db, current, params, event_id=event_id, winner=winner, status=status, date_from=date_from, date_to=date_to
    )


@router.get("/matches/{match_id}/history", response_model=MatchHistory)
async def history(match_id: uuid.UUID, current: Viewer, db: TenantDb) -> MatchHistory:
    return await service.history(db, current, match_id)


@router.post("/matches/{match_id}/declarations", response_model=DeclarationOut)
async def submit(match_id: uuid.UUID, body: DeclarationCreate, current: Submitter, db: TenantDb, meta: Meta) -> DeclarationOut:
    row = await service.submit(db, current, match_id, body, meta)
    return service.declaration_out(row, await service.usernames(db, current.tenant.id))


@router.post("/matches/{match_id}/betting/open", response_model=BettingControl)
async def open_betting(match_id: uuid.UUID, current: Submitter, db: TenantDb, meta: Meta) -> BettingControl:
    """Open or reopen simulated betting on the event's current fight (assigned Declarators only).

    Opening the next scheduled fight starts it. Idempotent: repeating it changes nothing."""
    return await service.open_betting(db, current, match_id, meta)


@router.post("/events/{event_id}/betting/open", response_model=BettingControl)
async def open_event_betting(event_id: uuid.UUID, current: Submitter, db: TenantDb, meta: Meta) -> BettingControl:
    """Open betting on the event's current fight; when the live card has none, add the next fight first.

    Assigned Declarators only. Idempotent while the fight's betting is open."""
    return await service.open_event_betting(db, current, event_id, meta)


@router.post("/matches/{match_id}/dummy-bets", response_model=BookOut)
async def add_dummy_stake(match_id: uuid.UUID, body: DummyStakeIn, current: Submitter, db: TenantDb, meta: Meta) -> BookOut:
    """Add a dummy MERON or WALA amount to the open fight.

    The amount is included in the all-player pool and the odds. It is not a player's bet and
    does not change any balance. Resending the same request_id does not add it twice."""
    return await service.add_dummy_stake(db, current, match_id, body, meta)


@router.put("/matches/{match_id}/odds", response_model=BookOut)
async def set_odds(match_id: uuid.UUID, body: OddsUpdate, current: Submitter, db: TenantDb, meta: Meta) -> BookOut:
    """Set Meron and Wala odds on an open fight, or reset them to the pool price.

    The two prices sum to at most 3.66. Accepted bets keep the odds they locked. The new price
    is broadcast to every connected player of this tenant."""
    return await service.set_odds(db, current, match_id, body, meta)


@router.post("/matches/{match_id}/betting/close", response_model=BettingControl)
async def close_betting(match_id: uuid.UUID, current: Submitter, db: TenantDb, meta: Meta) -> BettingControl:
    """Stop accepting new simulated bets on the fight; pools, bets and odds stay. Idempotent."""
    return await service.close_betting(db, current, match_id, meta)


@router.post("/events/{event_id}/fights", response_model=MatchOut, status_code=201)
async def prepare_fight(event_id: uuid.UUID, body: FightDetails, current: Submitter, db: TenantDb, meta: Meta) -> MatchOut:
    """Prepare the next sequential fight with the announced entries when the live card has none."""
    [out] = await matches.to_out(db, current.tenant.id, [await service.prepare_fight(db, current, event_id, body, meta)])
    return out


@router.patch("/matches/{match_id}", response_model=MatchOut)
async def update_fight(match_id: uuid.UUID, body: FightDetailsUpdate, current: Submitter, db: TenantDb, meta: Meta) -> MatchOut:
    """Enter or correct the entries of the prepared next fight (before betting first opens on it)."""
    [out] = await matches.to_out(db, current.tenant.id, [await service.update_fight(db, current, match_id, body, meta)])
    return out


# ---- Declarator desk: event header, fight controls, ghost betting ----


@router.get("/events/{event_id}/online", response_model=OnlinePlayers)
async def online_players(event_id: uuid.UUID, current: Viewer, db: TenantDb) -> OnlinePlayers:
    """Players connected to this workspace right now (one per player, however many tabs)."""
    return await desk.online_players(db, current, event_id)


@router.post("/events/{event_id}/end", response_model=EventOut)
async def end_event(event_id: uuid.UUID, current: Submitter, db: TenantDb, meta: Meta):  # type: ignore[no-untyped-def]
    """Complete the live event (assigned Declarator). A fight in progress must be declared or cancelled first."""
    event = await desk.end_event(db, current, event_id, meta)
    [out] = await events_service.to_out(db, current.tenant.id, [event])
    return out


@router.post("/events/{event_id}/refresh", response_model=Message)
async def refresh_all(event_id: uuid.UUID, current: Submitter, db: TenantDb, meta: Meta) -> Message:
    """Ask every connected screen (players and staff) to reload this event."""
    await desk.refresh_all(db, current, event_id, meta)
    return Message(message="Refresh sent to every connected screen")


@router.post("/events/{event_id}/jump", response_model=MatchOut)
async def jump_to_fight(event_id: uuid.UUID, body: JumpToFight, current: Submitter, db: TenantDb, meta: Meta) -> MatchOut:
    """Set the number of the next fight. Only while no fight is in progress; settled fights keep their numbers."""
    [out] = await matches.to_out(db, current.tenant.id, [await desk.jump_to_fight(db, current, event_id, body, meta)])
    return out


@router.put("/matches/{match_id}/teams", response_model=MatchOut)
async def change_teams(match_id: uuid.UUID, body: TeamNames, current: Submitter, db: TenantDb, meta: Meta) -> MatchOut:
    """Rename Team 1 (MERON) and Team 2 (WALA) of the current fight, until its result is declared."""
    [out] = await matches.to_out(db, current.tenant.id, [await desk.change_teams(db, current, match_id, body, meta)])
    return out


@router.get("/events/{event_id}/ghost-auto", response_model=GhostAutoOut)
async def ghost_status(event_id: uuid.UUID, current: Viewer, db: TenantDb) -> GhostAutoOut:
    return await desk.ghost_status(db, current, event_id)


@router.post("/events/{event_id}/ghost-auto/start", response_model=GhostAutoOut)
async def start_ghost(event_id: uuid.UUID, body: GhostAutoStart, current: Submitter, db: TenantDb, meta: Meta) -> GhostAutoOut:
    """Start automatic ghost betting on the open fight. Ghost amounts never touch a balance."""
    return await desk.start_ghost(db, current, event_id, body, meta)


@router.post("/events/{event_id}/ghost-auto/stop", response_model=GhostAutoOut)
async def stop_ghost(event_id: uuid.UUID, current: Submitter, db: TenantDb, meta: Meta) -> GhostAutoOut:
    """Stop automatic ghost betting. Repeating it changes nothing."""
    return await desk.stop_ghost_by_user(db, current, event_id, meta)


@router.post("/declarations/{declaration_id}/approve", response_model=DeclarationOut)
async def approve(declaration_id: uuid.UUID, current: Approver, db: TenantDb, meta: Meta) -> DeclarationOut:
    row = await service.review(db, current, declaration_id, "approve", None, meta)
    return service.declaration_out(row, await service.usernames(db, current.tenant.id))


@router.post("/declarations/{declaration_id}/review", response_model=DeclarationOut)
async def review(declaration_id: uuid.UUID, body: ReviewRequest, current: Approver, db: TenantDb, meta: Meta) -> DeclarationOut:
    row = await service.review(db, current, declaration_id, "review", body.reason, meta)
    return service.declaration_out(row, await service.usernames(db, current.tenant.id))


@router.post("/declarations/{declaration_id}/return", response_model=DeclarationOut)
async def return_declaration(
    declaration_id: uuid.UUID, body: ReviewRequest, current: Approver, db: TenantDb, meta: Meta
) -> DeclarationOut:
    row = await service.review(db, current, declaration_id, "return", body.reason, meta)
    return service.declaration_out(row, await service.usernames(db, current.tenant.id))


@router.get("/events/{event_id}/assignments", response_model=list[AssignmentOut])
async def assignments(event_id: uuid.UUID, current: Manager, db: TenantDb) -> list[AssignmentOut]:
    return await service.assignments(db, current, event_id)


@router.put("/events/{event_id}/assignments/{user_id}", response_model=Message)
async def assign(event_id: uuid.UUID, user_id: uuid.UUID, current: Manager, db: TenantDb, meta: Meta) -> Message:
    await service.assign(db, current, event_id, user_id, False, meta)
    return Message(message="Declarator assigned")


@router.delete("/events/{event_id}/assignments/{user_id}", response_model=Message)
async def unassign(event_id: uuid.UUID, user_id: uuid.UUID, current: Manager, db: TenantDb, meta: Meta) -> Message:
    await service.assign(db, current, event_id, user_id, True, meta)
    return Message(message="Assignment removed")


@router.get("/eligible-users", response_model=list[EligibleDeclarator])
async def eligible_users(current: Manager, db: TenantDb):
    from sqlalchemy import select

    from app.modules.auth.models import AccountStatus
    from app.modules.users.models import AccountType, TenantUser

    return list(
        (
            await db.execute(
                select(TenantUser)
                .where(
                    TenantUser.tenant_id == current.tenant.id,
                    TenantUser.account_type == AccountType.DECLARATOR,
                    TenantUser.status == AccountStatus.ACTIVE,
                    TenantUser.deleted_at.is_(None),
                )
                .order_by(TenantUser.username)
            )
        ).scalars()
    )
