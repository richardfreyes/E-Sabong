"""Tenant-scoped neutral simulation setup, controller, wagers and private settlement receipts."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.deps import CurrentUser, CurrentUserDep, Meta, TenantDb, require_any_permission, require_module, require_permissions
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.modules.audit import service as audit
from app.modules.auth.models import AccountStatus
from app.modules.betting.models import PlayWallet
from app.modules.betting.schemas import WalletOut
from app.modules.betting.service import wallet_for, wallet_out
from app.modules.simulations import service
from app.modules.simulations.access import TOURNAMENT_ROLE, visible_arena
from app.modules.simulations.models import (
    SimulationArena,
    SimulationController,
    SimulationMatch,
    SimulationSettlement,
    SimulationStake,
)
from app.modules.simulations.schemas import (
    ArenaCreate,
    ArenaOut,
    ArenaUpdate,
    MatchCreate,
    MatchView,
    OwnReceipt,
    SettlementView,
    SimulationCapabilities,
    TransitionIn,
    TransitionOut,
    WagerIn,
    WagerOut,
)
from app.modules.simulations.transactions import serializable
from app.modules.users.models import AccountType, TenantUser
from app.shared.schemas import Message, Page, PageDep, paginate

router = APIRouter(
    prefix="/simulations",
    tags=["simulated tournaments"],
    dependencies=[Depends(require_module("events")), Depends(require_module("matches"))],
)
Manager = Annotated[CurrentUser, Depends(require_permissions(P.SIMULATIONS_MANAGE))]
Viewer = Annotated[CurrentUser, Depends(require_any_permission(P.SIMULATIONS_MANAGE, P.SIMULATIONS_CONTROL))]


@router.get("/capabilities", response_model=SimulationCapabilities)
async def capabilities(current: CurrentUserDep) -> SimulationCapabilities:
    return SimulationCapabilities(
        role=TOURNAMENT_ROLE.get(current.user.account_type),
        permissions=sorted(p for p in current.permissions if p.startswith("simulations.")),
    )


@router.post("/arenas", response_model=ArenaOut, status_code=201)
async def create_arena(body: ArenaCreate, current: Manager, db: TenantDb, meta: Meta) -> SimulationArena:
    only_admin(current)
    arena_id = (
        await db.execute(
            insert(SimulationArena)
            .values(tenant_id=current.tenant.id, name=body.name)
            .on_conflict_do_nothing(constraint="uq_simulation_arenas_name")
            .returning(SimulationArena.id)
        )
    ).scalar_one_or_none()
    if arena_id is None:
        raise Conflict("An arena with this name already exists", code="arena_exists")
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=current.tenant.id,
        action="simulation.arena_created",
        resource_type="simulation_arena",
        resource_id=arena_id,
        changes={"name": body.name},
        meta=meta,
    )
    return await visible_arena(db, current, arena_id)


@router.get("/arenas", response_model=Page[ArenaOut])
async def list_arenas(current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[ArenaOut]:
    stmt = select(SimulationArena).where(SimulationArena.tenant_id == current.tenant.id)
    if current.user.account_type == AccountType.ADMIN and current.has(P.SIMULATIONS_MANAGE):
        pass
    elif current.user.account_type in (AccountType.PLAYER, AccountType.MASTER_AGENT, AccountType.AGENT):
        stmt = stmt.where(SimulationArena.enabled.is_(True))
    elif current.user.account_type == AccountType.DECLARATOR and current.has(P.SIMULATIONS_CONTROL):
        stmt = stmt.join(SimulationController, SimulationController.arena_id == SimulationArena.id).where(
            SimulationController.tenant_id == current.tenant.id,
            SimulationController.user_id == current.id,
        )
    else:
        raise Forbidden("Simulation arena access is not permitted")
    if params.q:
        stmt = stmt.where(SimulationArena.name.ilike(f"%{params.q}%"))
    rows, total = await paginate(db, stmt.order_by(SimulationArena.created_at, SimulationArena.id), params)
    return Page(items=[ArenaOut.model_validate(row) for row in rows], total=total, page=params.page, page_size=params.page_size)


@router.get("/arenas/{arena_id}", response_model=ArenaOut)
async def get_arena(arena_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> SimulationArena:
    return await service.arena_access(db, current, arena_id)


@router.put("/arenas/{arena_id}/controllers/{user_id}", response_model=Message)
async def assign_controller(arena_id: uuid.UUID, user_id: uuid.UUID, current: Manager, db: TenantDb, meta: Meta) -> Message:
    only_admin(current)
    await visible_arena(db, current, arena_id)
    user = await db.scalar(
        select(TenantUser)
        .where(
            TenantUser.tenant_id == current.tenant.id,
            TenantUser.id == user_id,
            TenantUser.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if user is None:
        raise NotFound("Account not found")
    if user.account_type != AccountType.DECLARATOR or user.status != AccountStatus.ACTIVE:
        raise Unprocessable("Assign an active Declarator account", code="invalid_controller")
    inserted = (
        await db.execute(
            insert(SimulationController)
            .values(
                tenant_id=current.tenant.id,
                arena_id=arena_id,
                user_id=user_id,
            )
            .on_conflict_do_nothing()
            .returning(SimulationController.user_id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        await audit.record(
            db,
            actor=current.actor,
            tenant_id=current.tenant.id,
            action="simulation.controller_assigned",
            resource_type="simulation_arena",
            resource_id=arena_id,
            changes={"user_id": user_id},
            meta=meta,
        )
    return Message(message="Controller assigned")


@router.delete("/arenas/{arena_id}/controllers/{user_id}", response_model=Message)
async def revoke_controller(arena_id: uuid.UUID, user_id: uuid.UUID, current: Manager, db: TenantDb, meta: Meta) -> Message:
    only_admin(current)
    await visible_arena(db, current, arena_id)
    removed = (
        await db.execute(
            delete(SimulationController)
            .where(
                SimulationController.tenant_id == current.tenant.id,
                SimulationController.arena_id == arena_id,
                SimulationController.user_id == user_id,
            )
            .returning(SimulationController.user_id)
        )
    ).scalar_one_or_none()
    if removed is not None:
        await audit.record(
            db,
            actor=current.actor,
            tenant_id=current.tenant.id,
            action="simulation.controller_removed",
            resource_type="simulation_arena",
            resource_id=arena_id,
            changes={"user_id": user_id},
            meta=meta,
        )
    return Message(message="Controller removed")


def only_admin(current: CurrentUser) -> None:
    if current.user.account_type != AccountType.ADMIN or not current.has(P.SIMULATIONS_MANAGE):
        raise Forbidden("Only the tenant administrator can manage simulation setup")


@router.patch("/arenas/{arena_id}", response_model=ArenaOut)
async def configure_arena(arena_id: uuid.UUID, body: ArenaUpdate, current: Manager, meta: Meta, db: TenantDb) -> ArenaOut:
    async def apply(db: TenantDb) -> ArenaOut:
        actor = await service.actor_now(db, current)
        only_admin(actor)
        arena = await db.scalar(
            select(SimulationArena)
            .where(
                SimulationArena.tenant_id == actor.tenant.id,
                SimulationArena.id == arena_id,
            )
            .with_for_update()
        )
        if arena is None:
            raise NotFound("Simulation arena not found")
        if arena.enabled != body.enabled:
            arena.enabled = body.enabled
            await audit.record(
                db,
                actor=actor.actor,
                tenant_id=actor.tenant.id,
                action="simulation.arena_configured",
                resource_type="simulation_arena",
                resource_id=arena.id,
                changes={"enabled": body.enabled},
                meta=meta,
            )
        return ArenaOut.model_validate(arena)

    await db.commit()  # Release the authentication read before opening a fresh serializable mutation.
    return await serializable(current.tenant.id, arena_id, apply)


@router.post("/arenas/{arena_id}/matches", response_model=MatchView, status_code=201)
async def create_match(arena_id: uuid.UUID, body: MatchCreate, current: Manager, db: TenantDb, meta: Meta) -> MatchView:
    only_admin(current)
    await service.arena_access(db, current, arena_id, control=True)
    match_id = (
        await db.execute(
            insert(SimulationMatch)
            .values(
                tenant_id=current.tenant.id,
                arena_id=arena_id,
                **body.model_dump(),
            )
            .on_conflict_do_nothing(constraint="uq_simulation_matches_number")
            .returning(SimulationMatch.id)
        )
    ).scalar_one_or_none()
    if match_id is None:
        raise Conflict("This match number already exists", code="match_exists")
    match = await db.get(SimulationMatch, match_id)
    assert match is not None
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=current.tenant.id,
        action="simulation.match_created",
        resource_type="simulation_match",
        resource_id=match.id,
        changes=body.model_dump(mode="json"),
        meta=meta,
    )
    return await service.view(db, match)


@router.get("/arenas/{arena_id}/matches", response_model=Page[MatchView])
async def list_matches(arena_id: uuid.UUID, current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[MatchView]:
    await service.arena_access(db, current, arena_id)
    rows, total = await paginate(
        db,
        select(SimulationMatch)
        .where(
            SimulationMatch.tenant_id == current.tenant.id,
            SimulationMatch.arena_id == arena_id,
        )
        .order_by(SimulationMatch.number),
        params,
    )
    return Page(items=[await service.view(db, row) for row in rows], total=total, page=params.page, page_size=params.page_size)


@router.get("/matches/{match_id}", response_model=MatchView)
async def read_match(match_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> MatchView:
    _, match = await service.match_access(db, current, match_id)
    return await service.view(db, match)


@router.post("/matches/{match_id}/transition", response_model=TransitionOut)
async def transition_match(
    match_id: uuid.UUID, body: TransitionIn, current: CurrentUserDep, meta: Meta, db: TenantDb
) -> TransitionOut:
    await db.commit()
    return await serializable(current.tenant.id, match_id, lambda db: service.transition(db, current, match_id, body, meta))


@router.post("/matches/{match_id}/wagers", response_model=WagerOut, status_code=201)
async def place_wager(match_id: uuid.UUID, body: WagerIn, current: CurrentUserDep, meta: Meta, db: TenantDb) -> WagerOut:
    await db.commit()
    return await serializable(current.tenant.id, match_id, lambda db: service.wager(db, current, match_id, body, meta))


@router.get("/matches/{match_id}/settlement", response_model=SettlementView)
async def read_settlement(match_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> SettlementView:
    await service.match_access(db, current, match_id)
    receipt = await db.scalar(
        select(SimulationSettlement).where(
            SimulationSettlement.tenant_id == current.tenant.id,
            SimulationSettlement.match_id == match_id,
        )
    )
    if receipt is None:
        raise NotFound("Settlement receipt not found")
    return SettlementView.model_validate(receipt)


@router.get("/matches/{match_id}/receipts", response_model=list[OwnReceipt])
async def own_receipts(match_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> list[OwnReceipt]:
    participated = await db.scalar(
        select(SimulationStake.id)
        .where(
            SimulationStake.tenant_id == current.tenant.id,
            SimulationStake.match_id == match_id,
            SimulationStake.player_id == current.id,
        )
        .limit(1)
    )
    if participated is None:
        raise NotFound("No settlement receipt for this participant")
    receipt = await db.scalar(
        select(SimulationSettlement).where(
            SimulationSettlement.tenant_id == current.tenant.id,
            SimulationSettlement.match_id == match_id,
        )
    )
    if receipt is None:
        return []
    return [OwnReceipt.model_validate(row) for row in receipt.lines.values() if row["player_id"] == str(current.id)]


@router.get("/wallet", response_model=WalletOut)
async def own_wallet(current: CurrentUserDep, db: TenantDb) -> WalletOut:
    if current.user.account_type != AccountType.PLAYER:
        raise Forbidden("Only players have a gameplay balance", code="players_only")
    active = await db.scalar(
        select(SimulationArena.id)
        .where(
            SimulationArena.tenant_id == current.tenant.id,
            SimulationArena.enabled.is_(True),
        )
        .limit(1)
    )
    if active is not None:
        wallet = await wallet_for(db, current.tenant.id, current.id, lock=True)
    else:
        wallet = await db.scalar(
            select(PlayWallet).where(
                PlayWallet.tenant_id == current.tenant.id,
                PlayWallet.user_id == current.id,
            )
        )
        if wallet is None:
            raise NotFound("No Arena Points allocation yet")
    return wallet_out(wallet)
