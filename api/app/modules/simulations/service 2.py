"""Neutral tournament operations. The database transaction is the sole state authority."""

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import exists, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, load_permissions, revoked_family_key
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.betting.models import PlayWallet, PlayWalletEntry, WalletEntryKind
from app.modules.betting.service import _post, wallet_for, wallet_out
from app.modules.simulations import broadcast, math
from app.modules.simulations.models import (
    SimulationArena,
    SimulationCommand,
    SimulationController,
    SimulationMatch,
    SimulationSettlement,
    SimulationSide,
    SimulationStake,
    SimulationState,
)
from app.modules.simulations.schemas import MatchView, SettlementView, TransitionIn, TransitionOut, WagerIn, WagerOut
from app.modules.users.models import AccountType, TenantUser

TRANSITIONS = {
    SimulationState.PENDING: {SimulationState.WAGERING_OPEN},
    SimulationState.WAGERING_OPEN: {SimulationState.FINAL_CALL, SimulationState.WAGERING_CLOSED},
    SimulationState.FINAL_CALL: {SimulationState.WAGERING_OPEN, SimulationState.WAGERING_CLOSED},
    SimulationState.WAGERING_CLOSED: {SimulationState.SIMULATION_RUNNING},
    SimulationState.SIMULATION_RUNNING: {SimulationState.RESULT_DECLARED},
    SimulationState.RESULT_DECLARED: {SimulationState.SETTLED},
    SimulationState.SETTLED: set(),
}


def room(tenant_id: uuid.UUID, arena_id: uuid.UUID) -> str:
    return f"tenant:{tenant_id}:arena:{arena_id}"


async def actor_now(db: AsyncSession, current: CurrentUser) -> CurrentUser:
    from app.core.redis import get_redis

    if await get_redis().exists(revoked_family_key(current.session_family)):
        raise Unauthorized("Session revoked")
    user = await db.get(TenantUser, current.id)
    if user is None or user.tenant_id != current.tenant.id or user.deleted_at or user.status != AccountStatus.ACTIVE:
        raise Unauthorized("Account is inactive")
    if user.must_change_password:
        raise Forbidden("Change your temporary password", code="password_change_required")
    return CurrentUser(user, await load_permissions(db, current.tenant.id, current.id), current.tenant, current.session_family)


async def arena_access(db: AsyncSession, actor: CurrentUser, arena_id: uuid.UUID, *, control: bool = False) -> SimulationArena:
    arena = await db.scalar(
        select(SimulationArena)
        .where(
            SimulationArena.tenant_id == actor.tenant.id,
            SimulationArena.id == arena_id,
        )
        .with_for_update(read=True)
    )
    if arena is None:
        raise NotFound("Simulation arena not found")
    if actor.user.account_type == AccountType.ADMIN and actor.has(P.SIMULATIONS_MANAGE):
        return arena
    if actor.user.account_type == AccountType.DECLARATOR and actor.has(P.SIMULATIONS_CONTROL):
        assigned = await db.scalar(
            select(SimulationController)
            .where(
                SimulationController.tenant_id == actor.tenant.id,
                SimulationController.arena_id == arena_id,
                SimulationController.user_id == actor.id,
            )
            .with_for_update(read=True)
        )
        if assigned:
            return arena
        raise NotFound("Simulation arena not found")
    if (
        not control
        and arena.enabled
        and actor.user.account_type
        in (
            AccountType.PLAYER,
            AccountType.MASTER_AGENT,
            AccountType.AGENT,
        )
    ):
        return arena
    raise Forbidden("This role cannot control the simulation" if control else "Arena access is not permitted")


async def match_access(
    db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, *, control: bool = False, lock: bool = False
) -> tuple[SimulationArena, SimulationMatch]:
    # Locate, authorize/lock the arena and assignment, then lock the match. All writers use
    # arena -> assignment -> match -> wallets ordered by UUID; never the reverse.
    match = await db.scalar(
        select(SimulationMatch).where(
            SimulationMatch.tenant_id == actor.tenant.id,
            SimulationMatch.id == match_id,
        )
    )
    if match is None:
        raise NotFound("Simulation match not found")
    arena = await arena_access(db, actor, match.arena_id, control=control)
    if lock:
        match = await db.scalar(
            select(SimulationMatch)
            .where(
                SimulationMatch.tenant_id == actor.tenant.id,
                SimulationMatch.id == match_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert match is not None
    return arena, match


async def view(db: AsyncSession, match: SimulationMatch) -> MatchView:
    a, b = math.odds(match.team_a_pool, match.team_b_pool, match.simulation_fee_rate)
    receipt = await db.scalar(
        select(SimulationSettlement.id).where(
            SimulationSettlement.tenant_id == match.tenant_id,
            SimulationSettlement.match_id == match.id,
        )
    )
    return MatchView(
        **{
            k: getattr(match, k)
            for k in MatchView.model_fields
            if k
            not in (
                "odds_a",
                "odds_b",
                "settlement_receipt_id",
            )
        },
        odds_a=str(a) if a is not None else None,
        odds_b=str(b) if b is not None else None,
        settlement_receipt_id=receipt,
    )


async def emit(db: AsyncSession, match: SimulationMatch, event: str, data: dict) -> None:
    from app.core.database import on_commit

    payload = {
        "type": event,
        "room": room(match.tenant_id, match.arena_id),
        "data": {"matchId": str(match.id), "version": match.version, **data},
    }
    channel = room(match.tenant_id, match.arena_id)

    async def send() -> None:
        await broadcast.deliver(channel, payload)

    on_commit(db, send)


async def settle(db: AsyncSession, match: SimulationMatch) -> SimulationSettlement:
    existing = await db.scalar(
        select(SimulationSettlement).where(
            SimulationSettlement.tenant_id == match.tenant_id,
            SimulationSettlement.match_id == match.id,
        )
    )
    if existing:
        return existing
    stakes = list(
        (
            await db.scalars(
                select(SimulationStake)
                .where(
                    SimulationStake.tenant_id == match.tenant_id,
                    SimulationStake.match_id == match.id,
                )
                .order_by(SimulationStake.created_at, SimulationStake.id)
            )
        ).all()
    )
    assert match.outcome is not None
    allocation = math.allocate([math.Line(s.id, s.side, s.amount) for s in stakes], match.outcome, match.simulation_fee_rate)
    if sum((s.amount for s in stakes), math.ZERO) != match.team_a_pool + match.team_b_pool:
        raise Conflict("Pool and stake records do not agree", code="pool_mismatch")
    wallet_rows = list(
        (
            await db.scalars(
                select(PlayWallet)
                .where(
                    PlayWallet.tenant_id == match.tenant_id,
                    PlayWallet.user_id.in_({s.player_id for s in stakes}),
                )
                .order_by(PlayWallet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    wallets = {w.user_id: w for w in wallet_rows}
    receipt = SimulationSettlement(
        tenant_id=match.tenant_id,
        match_id=match.id,
        outcome=match.outcome,
        total_staked=allocation.total,
        fee_amount=allocation.fee,
        distributed=allocation.total - allocation.fee,
        refunded=allocation.refunded,
        lines={},
    )
    lines = {}
    for stake in stakes:
        amount = allocation.payouts[stake.id]
        kind = (
            "SETTLEMENT_REFUND"
            if allocation.refunded
            else "SETTLEMENT_WIN"
            if stake.side.value == match.outcome.value
            else "SETTLEMENT_LOSS"
        )
        entry_id = None
        if amount > 0:
            key = f"simulation:settle:{match.id}:{stake.id}"
            await _post(
                db,
                wallets[stake.player_id],
                WalletEntryKind.REFUND if allocation.refunded else WalletEntryKind.PAYOUT,
                amount,
                key,
                bet_id=stake.id,
                memo=f"Simulation #{match.number}: {kind}",
            )
            entry_id = await db.scalar(
                select(PlayWalletEntry.id).where(
                    PlayWalletEntry.tenant_id == match.tenant_id,
                    PlayWalletEntry.idempotency_key == key,
                )
            )
        lines[str(stake.id)] = {
            "stake_id": str(stake.id),
            "player_id": str(stake.player_id),
            "kind": kind,
            "amount": str(amount),
            "ledger_entry_id": str(entry_id) if entry_id else None,
        }
    # Receipt insertion is deferred until all lines exist: the database prevents future edits.
    receipt.lines = lines
    db.add(receipt)
    await db.flush()
    return receipt


async def transition(
    db: AsyncSession, current: CurrentUser, match_id: uuid.UUID, data: TransitionIn, meta: RequestMeta
) -> TransitionOut:
    actor = await actor_now(db, current)
    arena, match = await match_access(db, actor, match_id, control=True, lock=True)
    digest = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    command = await db.scalar(
        select(SimulationCommand).where(
            SimulationCommand.tenant_id == actor.tenant.id,
            SimulationCommand.actor_id == actor.id,
            SimulationCommand.request_id == data.request_id,
        )
    )
    if command:
        if command.match_id != match_id or command.request_hash != digest:
            raise Conflict("Request ID was already used for another command", code="idempotency_conflict")
        return TransitionOut.model_validate({**command.response, "replayed": True})
    if match.state == SimulationState.SETTLED and data.new_state == SimulationState.SETTLED:
        receipt = await db.scalar(
            select(SimulationSettlement).where(
                SimulationSettlement.tenant_id == match.tenant_id,
                SimulationSettlement.match_id == match.id,
            )
        )
        if receipt is None:
            raise Conflict("Settlement receipt is missing", code="receipt_missing")
        return TransitionOut(match=await view(db, match), settlement=SettlementView.model_validate(receipt), replayed=True)
    if match.version != data.expected_version:
        raise Conflict("Match version has changed", code="stale_match", details={"version": match.version})
    if data.new_state not in TRANSITIONS[match.state]:
        raise Conflict("Transition is not allowed from this state", code="invalid_transition")
    if data.new_state == SimulationState.WAGERING_OPEN and not arena.enabled:
        raise Conflict("The arena is disabled", code="arena_disabled")
    previous = match.state
    match.state = data.new_state
    match.version += 1
    match.final_call_ends_at = (
        utcnow() + timedelta(seconds=data.final_call_seconds or 10) if match.state == SimulationState.FINAL_CALL else None
    )
    if data.outcome is not None:
        match.outcome = data.outcome
    receipt = await settle(db, match) if match.state == SimulationState.SETTLED else None
    await db.flush()
    result = TransitionOut(match=await view(db, match), settlement=SettlementView.model_validate(receipt) if receipt else None)
    db.add(
        SimulationCommand(
            tenant_id=actor.tenant.id,
            actor_id=actor.id,
            match_id=match.id,
            request_id=data.request_id,
            request_hash=digest,
            version=match.version,
            response=result.model_dump(mode="json"),
        )
    )
    await audit.record(
        db,
        actor=actor.actor,
        tenant_id=actor.tenant.id,
        action="simulation.transition",
        resource_type="simulation_match",
        resource_id=match.id,
        changes={
            "previous_state": previous,
            "state": match.state,
            "version": match.version,
            "settlement_receipt_id": receipt.id if receipt else None,
        },
        meta=meta,
    )
    await emit(
        db,
        match,
        "MATCH_STATE_CHANGED",
        {
            "previousState": previous.name,
            "newState": match.state.name,
            "timestamp": utcnow().isoformat(),
            "finalCallEndsAt": match.final_call_ends_at,
        },
    )
    return result


async def wager(db: AsyncSession, current: CurrentUser, match_id: uuid.UUID, data: WagerIn, meta: RequestMeta) -> WagerOut:
    actor = await actor_now(db, current)
    if actor.user.account_type != AccountType.PLAYER:
        raise Forbidden("Only player accounts can place simulated wagers", code="players_only")
    arena, match = await match_access(db, actor, match_id, lock=True)
    old = await db.scalar(
        select(SimulationStake).where(
            SimulationStake.tenant_id == actor.tenant.id,
            SimulationStake.player_id == actor.id,
            SimulationStake.request_id == data.request_id,
        )
    )
    if old:
        if old.match_id != match.id or old.side != data.side or old.amount != data.amount:
            raise Conflict("Request ID was already used for a different wager", code="idempotency_conflict")
        return WagerOut(
            id=old.id,
            match=await view(db, match),
            wallet=wallet_out(await wallet_for(db, actor.tenant.id, actor.id)),
            replayed=True,
        )
    if match.state != SimulationState.WAGERING_OPEN or not arena.enabled:
        raise Unprocessable("Wagering is closed", code="wagering_closed", status_code=400)
    wallet = await wallet_for(db, actor.tenant.id, actor.id, lock=True)
    if data.amount > wallet.balance:
        raise Unprocessable("Not enough Arena Points", code="insufficient_balance")
    column = SimulationMatch.team_a_pool if data.side.value == "team_a" else SimulationMatch.team_b_pool
    updated = await db.scalar(
        update(SimulationMatch)
        .where(
            SimulationMatch.tenant_id == actor.tenant.id,
            SimulationMatch.id == match.id,
            SimulationMatch.state == SimulationState.WAGERING_OPEN,
        )
        .values({column: column + data.amount, SimulationMatch.version: SimulationMatch.version + 1})
        .returning(SimulationMatch.id)
    )
    if updated is None:
        raise Unprocessable("Wagering is closed", code="wagering_closed", status_code=400)
    await db.refresh(match)
    stake_id = uuid.uuid4()
    inserted = await db.scalar(
        insert(SimulationStake)
        .from_select(
            ["id", "tenant_id", "match_id", "player_id", "request_id", "side", "amount"],
            select(
                literal(stake_id),
                literal(actor.tenant.id),
                literal(match.id),
                literal(actor.id),
                literal(data.request_id),
                literal(data.side.value),
                literal(data.amount),
            ).where(
                exists().where(
                    SimulationMatch.tenant_id == actor.tenant.id,
                    SimulationMatch.id == match.id,
                    SimulationMatch.state == SimulationState.WAGERING_OPEN,
                )
            ),
        )
        .returning(SimulationStake.id)
    )
    if inserted is None:
        raise Unprocessable("Wagering is closed", code="wagering_closed", status_code=400)
    stake = await db.get(SimulationStake, inserted)
    assert stake is not None
    await _post(
        db,
        wallet,
        WalletEntryKind.STAKE,
        -stake.amount,
        f"simulation:stake:{stake.id}",
        bet_id=stake.id,
        memo=f"Simulation #{match.number}: {data.side.name} stake",
    )
    await audit.record(
        db,
        actor=actor.actor,
        tenant_id=actor.tenant.id,
        action="simulation.wager_placed",
        resource_type="simulation_stake",
        resource_id=stake.id,
        changes={"match_id": match.id, "amount": str(data.amount), "side": data.side},
        meta=meta,
    )
    snapshot = await view(db, match)
    await emit(
        db,
        match,
        "ODDS_UPDATED",
        {
            "totalSideA": str(match.team_a_pool),
            "totalSideB": str(match.team_b_pool),
            "oddsSideA": snapshot.odds_a,
            "oddsSideB": snapshot.odds_b,
            # Consumed by the coalescer, which sums them across merged wagers.
            "deltaSideA": str(stake.amount if data.side == SimulationSide.TEAM_A else 0),
            "deltaSideB": str(stake.amount if data.side == SimulationSide.TEAM_B else 0),
        },
    )
    return WagerOut(id=stake.id, match=snapshot, wallet=wallet_out(wallet))
