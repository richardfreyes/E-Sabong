"""Declarator desk controls: event header, fight controls and ghost (dummy) betting.

Every action here is for the Declarator assigned to the event. The event row is locked first and
the fight second (the order every result path uses), so these never race a declaration.
Ghost amounts only ever move a fight's dummy pools; no player balance or wallet is touched.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, load_permissions
from app.core.errors import AppError, Conflict, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.core.tenancy import TenantContext
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.betting import service as betting
from app.modules.betting.models import Bet, BetStatus, BookState, PlayWallet
from app.modules.betting.schemas import DummyStakeIn
from app.modules.declarator import service as desk
from app.modules.declarator.models import GhostAutoBet, GhostAutoStatus
from app.modules.declarator.schemas import GhostAutoOut, GhostAutoStart, JumpToFight, OnlinePlayer, OnlinePlayers, TeamNames
from app.modules.events import service as events
from app.modules.events.models import Event, EventStatus
from app.modules.events.schemas import MatchCreate
from app.modules.matches import service as matches
from app.modules.matches.models import Match, MatchStatus
from app.modules.realtime import presence
from app.modules.realtime.publisher import publish_after_commit
from app.modules.results import service as results
from app.modules.tenants.models import Tenant
from app.modules.users.models import AccountType, TenantUser

log = structlog.get_logger("ghost")
ZERO = Decimal("0.00")


# ---- event header ----------------------------------------------------------------------------


async def online_players(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID) -> OnlinePlayers:
    """Connected players with their play balance and accepted stakes on the event's current fight.

    Read-only: nothing here touches a wallet. Players are this tenant's only (RLS plus the tenant filter).
    """
    event = await desk.access_event(db, actor, event_id)
    seen = await presence.online_player_ids(actor.tenant.id)
    target = desk.betting_target(await matches.list_for_event(db, actor.tenant.id, event.id))
    if not seen:
        return OnlinePlayers(players=0, fight_number=target.match_number if target else None)
    tenant_id = actor.tenant.id
    rows = (
        await db.execute(
            select(TenantUser.id, TenantUser.username, TenantUser.full_name, func.coalesce(PlayWallet.balance, ZERO))
            .outerjoin(PlayWallet, (PlayWallet.tenant_id == TenantUser.tenant_id) & (PlayWallet.user_id == TenantUser.id))
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.id.in_(seen),
                TenantUser.account_type == AccountType.PLAYER,
                TenantUser.deleted_at.is_(None),
            )
        )
    ).all()
    stakes: dict[tuple[uuid.UUID, str], Decimal] = {}
    if target:
        for user_id, side, amount in (
            await db.execute(
                select(Bet.user_id, Bet.side, func.sum(Bet.amount))
                .where(Bet.tenant_id == tenant_id, Bet.match_id == target.id, Bet.user_id.in_(seen))
                .where(Bet.status != BetStatus.REFUNDED)
                .group_by(Bet.user_id, Bet.side)
            )
        ).all():
            stakes[(user_id, str(side))] = amount
    items = [
        OnlinePlayer(
            id=user_id,
            username=username,
            full_name=full_name,
            balance=balance,
            meron_stake=stakes.get((user_id, "meron"), ZERO),
            wala_stake=stakes.get((user_id, "wala"), ZERO),
            draw_stake=stakes.get((user_id, "draw"), ZERO),
            last_seen_at=datetime.fromtimestamp(seen[user_id], UTC),
        )
        for user_id, username, full_name, balance in rows
    ]
    items.sort(key=lambda p: (-(p.meron_stake + p.wala_stake + p.draw_stake), p.username.lower()))
    return OnlinePlayers(players=len(items), fight_number=target.match_number if target else None, items=items)


async def end_event(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, meta: RequestMeta) -> Event:
    """Complete the live event. A fight still in progress must be declared or cancelled first."""
    event = await desk._declarator_desk_event(db, actor, event_id)
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Only a live event can be ended", code="event_not_live")
    await stop_ghost(db, actor.tenant.id, event.id, "Event ended", actor=actor, meta=meta)
    return await events.transition(db, actor, event.id, EventStatus.COMPLETED, meta)


async def refresh_all(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, meta: RequestMeta) -> None:
    """Tell every connected screen of this tenant to reload the event (players and staff)."""
    event = await desk._declarator_desk_event(db, actor, event_id)
    await audit.record(
        db,
        actor=actor.actor,
        action="event.refresh_broadcast",
        resource_type="event",
        resource_id=event.id,
        tenant_id=actor.tenant.id,
        changes={},
        meta=meta,
    )
    publish_after_commit(db, actor.tenant.id, "event.updated", {"id": event.id, "status": event.status, "name": event.name})


# ---- fight controls --------------------------------------------------------------------------


async def jump_to_fight(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, data: JumpToFight, meta: RequestMeta) -> Match:
    """Set the number of the next fight (e.g. continue the card at #196).

    Only while no fight is in progress. The prepared next fight is renumbered, or a new one is
    added. Numbers must be free and continue after every settled fight, so earlier results,
    settlements and history keep their numbers."""
    event = await desk._declarator_desk_event(db, actor, event_id)
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Fights are set on a live event", code="event_not_live")
    tenant_id = actor.tenant.id
    card = await matches.list_for_event(db, tenant_id, event.id)
    target = desk.betting_target(card)
    number = data.match_number
    if target is not None and target.status == MatchStatus.IN_PROGRESS:
        raise Conflict(
            f"Fight #{target.match_number} is in progress. Declare or cancel it before setting the next fight.",
            code="fight_in_progress",
        )
    settled = [m.match_number for m in card if m.status in (MatchStatus.COMPLETED, MatchStatus.CANCELLED)]
    if settled and number <= max(settled):
        raise Unprocessable(
            f"Fight numbers continue after #{max(settled)}. Choose #{max(settled) + 1} or higher.",
            code="fight_number_too_low",
            details={"minimum": max(settled) + 1},
        )
    taken = next((m for m in card if m.match_number == number and (target is None or m.id != target.id)), None)
    if taken is not None:
        raise Conflict(f"Fight #{number} already exists on this event", code="match_number_taken")
    earlier = [m.match_number for m in card if m.status == MatchStatus.SCHEDULED and m.match_number < number and m is not target]
    if earlier:
        raise Conflict(f"Fight #{min(earlier)} is scheduled before #{number}", code="fight_scheduled_before")
    if target is None:
        created = await matches.create_match(
            db, actor, event.id, MatchCreate(match_number=number, meron_name="Meron", wala_name="Wala"), meta
        )
        await audit.record(
            db,
            actor=actor.actor,
            action="match.next_prepared",
            resource_type="match",
            resource_id=created.id,
            tenant_id=tenant_id,
            changes={"event_id": event.id, "match_number": number, "by": "declarator", "on": "jump_to_fight"},
            meta=meta,
        )
        return created
    match = await matches.get_match(db, tenant_id, target.id, for_update=True)
    if await betting.book_for(db, tenant_id, match.id) is not None:
        raise Conflict("Betting has opened on this fight; its number is locked", code="match_locked")
    if match.match_number != number:
        before = match.match_number
        match.match_number = number
        await db.flush()
        await audit.record(
            db,
            actor=actor.actor,
            action="match.renumbered",
            resource_type="match",
            resource_id=match.id,
            tenant_id=tenant_id,
            changes={"match_number": [before, number], "by": "declarator"},
            meta=meta,
        )
        publish_after_commit(db, tenant_id, "match.updated", matches.realtime_payload(match))
    return match


async def change_teams(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: TeamNames, meta: RequestMeta) -> Match:
    """Rename Team 1 (MERON) and Team 2 (WALA) of the current fight, until its result is declared.

    Settled fights are never changed, so earlier results and history stay as recorded."""
    event, match = await desk._betting_desk(db, actor, match_id)
    target = desk.betting_target(await matches.list_for_event(db, actor.tenant.id, event.id))
    if target is None or target.id != match.id:
        raise Conflict("Only the current fight's teams can be changed", code="not_current_fight")
    if match.status not in (MatchStatus.SCHEDULED, MatchStatus.IN_PROGRESS) or await results.current_result(
        db, actor.tenant.id, match.id
    ):
        raise Conflict(f"Fight #{match.match_number} already has a result; its teams are locked", code="result_finalized")
    meron, wala = data.meron_name.strip(), data.wala_name.strip()
    if not meron or not wala:
        raise Unprocessable("Enter both team names", code="team_name_required")
    before = {"meron_name": match.meron_name, "wala_name": match.wala_name}
    if before == {"meron_name": meron, "wala_name": wala}:
        return match
    match.meron_name, match.wala_name = meron, wala
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="match.teams_changed",
        resource_type="match",
        resource_id=match.id,
        tenant_id=actor.tenant.id,
        changes=audit.diff(before, {"meron_name": meron, "wala_name": wala}),
        meta=meta,
    )
    publish_after_commit(db, actor.tenant.id, "match.updated", matches.realtime_payload(match))
    return match


# ---- automatic ghost betting -----------------------------------------------------------------


async def _active_job(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID, *, lock: bool = False) -> GhostAutoBet | None:
    stmt = select(GhostAutoBet).where(
        GhostAutoBet.tenant_id == tenant_id, GhostAutoBet.event_id == event_id, GhostAutoBet.status == GhostAutoStatus.ACTIVE
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _notify_desk(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
    for uid in await desk.audience(db, tenant_id, event_id):
        publish_after_commit(db, tenant_id, "ghost.updated", {"event_id": event_id}, user_id=uid)


async def ghost_status(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID) -> GhostAutoOut:
    await desk.access_event(db, actor, event_id)
    return await _ghost_out(db, actor.tenant.id, event_id)


async def _ghost_out(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID) -> GhostAutoOut:
    row = (
        await db.execute(
            select(GhostAutoBet, Match.match_number, TenantUser.username)
            .join(Match, (Match.tenant_id == GhostAutoBet.tenant_id) & (Match.id == GhostAutoBet.match_id))
            .join(TenantUser, (TenantUser.tenant_id == GhostAutoBet.tenant_id) & (TenantUser.id == GhostAutoBet.started_by_id))
            .where(GhostAutoBet.tenant_id == tenant_id, GhostAutoBet.event_id == event_id)
            .order_by((GhostAutoBet.status == GhostAutoStatus.ACTIVE).desc(), GhostAutoBet.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return GhostAutoOut(active=False)
    job, number, username = row
    return GhostAutoOut(
        active=job.status == GhostAutoStatus.ACTIVE,
        id=job.id,
        match_id=job.match_id,
        match_number=number,
        meron_amount=job.meron_amount,
        wala_amount=job.wala_amount,
        interval_seconds=job.interval_seconds,
        runs=job.runs,
        added_meron=job.added_meron,
        added_wala=job.added_wala,
        started_by=username,
        started_at=job.created_at,
        next_run_at=job.next_run_at if job.status == GhostAutoStatus.ACTIVE else None,
        stopped_at=job.stopped_at,
        stop_reason=job.stop_reason,
    )


async def start_ghost(
    db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, data: GhostAutoStart, meta: RequestMeta
) -> GhostAutoOut:
    """Start automatic ghost betting on the current fight while its betting is open. One job per event."""
    event = await desk._declarator_desk_event(db, actor, event_id)
    tenant_id = actor.tenant.id
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Auto ghost betting runs on a live event", code="event_not_live")
    if await _active_job(db, tenant_id, event.id, lock=True):
        raise Conflict("Auto ghost betting is already running on this event. Stop it first.", code="ghost_auto_running")
    target = desk.betting_target(await matches.list_for_event(db, tenant_id, event.id))
    book = await betting.book_for(db, tenant_id, target.id) if target else None
    if target is None or book is None or book.state != BookState.OPEN:
        raise Conflict("Open betting before starting auto ghost betting", code="betting_closed")
    job = GhostAutoBet(
        tenant_id=tenant_id,
        event_id=event.id,
        match_id=target.id,
        started_by_id=actor.id,
        meron_amount=data.meron_amount,
        wala_amount=data.wala_amount,
        interval_seconds=data.interval_seconds,
        status=GhostAutoStatus.ACTIVE,
        runs=0,
        added_meron=ZERO,
        added_wala=ZERO,
        next_run_at=utcnow(),
    )
    db.add(job)
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="ghost.auto_started",
        resource_type="match",
        resource_id=target.id,
        tenant_id=tenant_id,
        changes={
            "job_id": job.id,
            "meron_amount": str(data.meron_amount),
            "wala_amount": str(data.wala_amount),
            "interval_seconds": data.interval_seconds,
        },
        meta=meta,
    )
    await _notify_desk(db, tenant_id, event.id)
    return await _ghost_out(db, tenant_id, event.id)


async def stop_ghost(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    reason: str,
    *,
    actor: CurrentUser | None = None,
    meta: RequestMeta | None = None,
) -> bool:
    """Stop the event's running job, if any. The caller holds the event lock. Returns whether one stopped."""
    job = await _active_job(db, tenant_id, event_id, lock=True)
    if job is None:
        return False
    job.status = GhostAutoStatus.STOPPED
    job.stopped_at = utcnow()
    job.stop_reason = reason[:200]
    await db.flush()
    if actor is not None:
        await audit.record(
            db,
            actor=actor.actor,
            action="ghost.auto_stopped",
            resource_type="match",
            resource_id=job.match_id,
            tenant_id=tenant_id,
            changes={"job_id": job.id, "reason": job.stop_reason, "runs": job.runs},
            meta=meta or RequestMeta(),
        )
    await _notify_desk(db, tenant_id, event_id)
    return True


async def stop_ghost_by_user(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, meta: RequestMeta) -> GhostAutoOut:
    event = await desk._declarator_desk_event(db, actor, event_id)
    await stop_ghost(db, actor.tenant.id, event.id, f"Stopped by {actor.user.username}", actor=actor, meta=meta)
    return await _ghost_out(db, actor.tenant.id, event.id)


async def stop_ghost_for_match(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, match: Match, reason: str) -> None:
    """Betting on ``match`` closed or its result was declared: stop the job running on it."""
    job = await _active_job(db, actor.tenant.id, event_id)
    if job is not None and job.match_id == match.id:
        await stop_ghost(db, actor.tenant.id, event_id, reason, actor=actor)


# ---- ghost runner ----------------------------------------------------------------------------
#
# Runs inside the API process. Every second one instance (a short Redis lock picks it) finds due
# jobs with the platform role, then runs each inside its own tenant session as the Declarator who
# started it, re-checking every rule (assignment, permission, live event, open betting) each time.
# Each run uses request ids derived from the job and run number, so a retried run never adds twice.

RUNNER_LOCK = "ghost:runner"
RUNNER_TICK_SECONDS = 1.0
GHOST_META = RequestMeta(user_agent="ghost-runner")


class _Stop(Exception):  # noqa: N818 - control flow: stop the job with this reason
    pass


async def _desk_actor(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> CurrentUser:
    tenant = await db.get(Tenant, tenant_id)
    user = await db.get(TenantUser, user_id)
    if tenant is None or user is None or user.deleted_at is not None or user.status != AccountStatus.ACTIVE:
        raise _Stop("The Declarator account is no longer active")
    perms = await load_permissions(db, tenant_id, user.id)
    if user.account_type != AccountType.DECLARATOR or P.RESULTS_SUBMIT not in perms:
        raise _Stop("The Declarator no longer has permission to bet")
    context = TenantContext(id=tenant.id, slug=tenant.slug, name=tenant.name, status=tenant.status)
    return CurrentUser(user=user, permissions=perms, tenant=context, session_family=uuid.UUID(int=0))


async def run_job(db: AsyncSession, tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """One run of one job, if it is due. Raises ``_Stop`` when the job must stop."""
    job = await db.get(GhostAutoBet, job_id)
    if job is None or job.status != GhostAutoStatus.ACTIVE or job.next_run_at > utcnow():
        return
    actor = await _desk_actor(db, tenant_id, job.started_by_id)
    try:
        event, match = await desk._betting_desk(db, actor, job.match_id)
    except AppError as e:  # not assigned any more, fight gone, ...
        raise _Stop(e.message) from e
    if event.status != EventStatus.LIVE:
        raise _Stop("Event is no longer live")
    book = await betting.book_for(db, tenant_id, match.id)
    if book is None or book.state != BookState.OPEN:
        raise _Stop(f"Betting closed on Fight #{match.match_number}")
    # Lock the job after the event and fight (the order Start/Stop use), then re-check it.
    job = (
        await db.execute(
            select(GhostAutoBet)
            .where(GhostAutoBet.tenant_id == tenant_id, GhostAutoBet.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    now = utcnow()
    if job.status != GhostAutoStatus.ACTIVE or job.next_run_at > now:
        return
    sides: tuple[tuple[Literal["meron", "wala"], Decimal], ...] = (("meron", job.meron_amount), ("wala", job.wala_amount))
    for side, amount in sides:
        if amount > 0:
            request_id = uuid.uuid5(job.id, f"{job.runs}:{side}")
            await betting.add_dummy_stake(
                db, actor, match.id, DummyStakeIn(side=side, amount=amount, request_id=request_id), GHOST_META
            )
    job.runs += 1
    job.added_meron += job.meron_amount
    job.added_wala += job.wala_amount
    job.next_run_at = now + timedelta(seconds=job.interval_seconds)
    await db.flush()


async def _stop_after_failure(tenant_id: uuid.UUID, job_id: uuid.UUID, reason: str) -> None:
    from app.core.database import tenant_session

    async with tenant_session(tenant_id) as db:
        job = await db.get(GhostAutoBet, job_id)
        if job is None or job.status != GhostAutoStatus.ACTIVE:
            return
        await stop_ghost(db, tenant_id, job.event_id, reason)
        await audit.record(
            db,
            actor=audit.Actor(audit.ActorType.SYSTEM, None, "ghost-runner"),
            action="ghost.auto_stopped",
            resource_type="match",
            resource_id=job.match_id,
            tenant_id=tenant_id,
            changes={"job_id": job.id, "reason": job.stop_reason, "runs": job.runs},
            meta=GHOST_META,
        )


async def run_due_jobs() -> int:
    """Run every due job once. Returns how many were due."""
    from app.core.database import platform_session, tenant_session

    async with platform_session() as pdb:
        due = (
            await pdb.execute(
                select(GhostAutoBet.tenant_id, GhostAutoBet.id)
                .where(GhostAutoBet.status == GhostAutoStatus.ACTIVE, GhostAutoBet.next_run_at <= func.now())
                .order_by(GhostAutoBet.next_run_at)
                .limit(200)
            )
        ).all()
    for tenant_id, job_id in due:
        try:
            async with tenant_session(tenant_id) as db:
                await run_job(db, tenant_id, job_id)
        except _Stop as stop:
            await _stop_after_failure(tenant_id, job_id, str(stop))
        except AppError as e:
            await _stop_after_failure(tenant_id, job_id, e.message)
        except Exception:  # noqa: BLE001 - one bad job must not stop the others
            log.exception("ghost_job_failed", job_id=str(job_id))
    return len(due)


async def runner(stop: asyncio.Event) -> None:
    from app.core.redis import get_redis

    instance = uuid.uuid4().hex
    while not stop.is_set():
        try:
            if await get_redis().set(RUNNER_LOCK, instance, nx=True, px=int(RUNNER_TICK_SECONDS * 900)):
                await run_due_jobs()
        except Exception:  # noqa: BLE001 - keep ticking through transient DB/Redis errors
            log.exception("ghost_runner_tick_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=RUNNER_TICK_SECONDS)
