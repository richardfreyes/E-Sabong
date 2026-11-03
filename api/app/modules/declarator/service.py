"""Two-person result verification, separate from the official result projection."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import String, case, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.betting import reports as betting_reports
from app.modules.betting import service as betting
from app.modules.betting.models import BookState
from app.modules.declarator.models import (
    ACTIVE_STATUSES,
    DeclarationAudit,
    DeclarationOutcome,
    DeclarationStatus,
    EventDeclarator,
    ResultDeclaration,
)
from app.modules.declarator.schemas import (
    AssignmentOut,
    AuditOut,
    BettingControl,
    DeclarationCreate,
    DeclarationOut,
    FightDetails,
    FightDetailsUpdate,
    MatchHistory,
    MatchRecord,
    MonitorOut,
)
from app.modules.events import service as events
from app.modules.events.models import PUBLISHED_EVENT_STATUSES, Event, EventStatus
from app.modules.events.schemas import MatchCreate, MatchUpdate, ResultHistoryOut
from app.modules.matches import service as matches
from app.modules.matches.models import Match, MatchStatus
from app.modules.notifications.service import notify
from app.modules.realtime.publisher import publish_after_commit
from app.modules.results import service as results
from app.modules.results.models import MatchOutcome, MatchResult
from app.modules.users.models import AccountType, RolePermission, TenantUser, UserRole
from app.shared.repository import get_scoped
from app.shared.schemas import Page, PageParams, paginate

VISIBLE_EVENT_STATUSES = (*PUBLISHED_EVENT_STATUSES, EventStatus.CANCELLED)


def is_approver(actor: CurrentUser) -> bool:
    return actor.has(P.RESULTS_APPROVE)


def assignment_filter(actor: CurrentUser):  # type: ignore[no-untyped-def]
    return exists(
        select(EventDeclarator.id).where(
            EventDeclarator.tenant_id == actor.tenant.id,
            EventDeclarator.event_id == Event.id,
            EventDeclarator.user_id == actor.id,
        )
    )


async def access_event(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, *, lock: bool = False) -> Event:
    event = await get_scoped(db, Event, event_id, actor.tenant.id, for_update=lock)
    if event is None:
        raise NotFound("Event not found")
    if not is_approver(actor):
        if actor.user.account_type != AccountType.DECLARATOR or not actor.has(P.RESULTS_SUBMIT):
            raise Forbidden("Declarator access required")
        assignment = (
            await db.execute(
                select(EventDeclarator.id).where(
                    EventDeclarator.tenant_id == actor.tenant.id,
                    EventDeclarator.event_id == event.id,
                    EventDeclarator.user_id == actor.id,
                )
            )
        ).first()
        if not assignment or event.status not in VISIBLE_EVENT_STATUSES:
            raise NotFound("Assigned event not found")
    return event


async def assigned_events(db: AsyncSession, actor: CurrentUser):  # type: ignore[no-untyped-def]
    stmt = select(Event).where(Event.tenant_id == actor.tenant.id, Event.deleted_at.is_(None))
    if not is_approver(actor):
        if actor.user.account_type != AccountType.DECLARATOR:
            raise Forbidden("Declarator access required")
        stmt = stmt.where(assignment_filter(actor), Event.status.in_(VISIBLE_EVENT_STATUSES))
    rows = (await db.execute(stmt.order_by(Event.starts_at.desc(), Event.id))).scalars().all()
    return await events.to_out(db, actor.tenant.id, list(rows))


async def usernames(db: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return dict(
        (
            await db.execute(
                select(TenantUser.id, TenantUser.username).where(
                    TenantUser.tenant_id == tenant_id,
                )
            )
        )
        .tuples()
        .all()
    )


def declaration_out(row: ResultDeclaration, names: dict[uuid.UUID, str]) -> DeclarationOut:
    return DeclarationOut(
        id=row.id,
        match_id=row.match_id,
        outcome=row.outcome,
        status=row.status,
        base_revision=row.base_revision,
        reason=row.reason,
        declared_by_id=row.declared_by_id,
        declared_by=names.get(row.declared_by_id, "Unavailable account"),
        declared_at=row.created_at,
        approved_by_id=row.approved_by_id,
        approved_by=names.get(row.approved_by_id),
        finalized_at=row.finalized_at,
    )


async def latest_declaration(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> ResultDeclaration | None:
    return (
        await db.execute(
            select(ResultDeclaration)
            .where(
                ResultDeclaration.tenant_id == tenant_id,
                ResultDeclaration.match_id == match_id,
            )
            .order_by(ResultDeclaration.created_at.desc(), ResultDeclaration.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def record(db: AsyncSession, actor: CurrentUser, match: Match, *, names=None, event=None) -> MatchRecord:  # type: ignore[no-untyped-def]
    names = names if names is not None else await usernames(db, actor.tenant.id)
    event = event or await access_event(db, actor, match.event_id)
    [card] = await matches.to_out(db, actor.tenant.id, [match])
    decl = await latest_declaration(db, actor.tenant.id, match.id)
    official = await results.current_result(db, actor.tenant.id, match.id)
    return format_record(match, card, decl, official, event.name, names)


def format_record(match, card, decl, official, event_name, names) -> MatchRecord:  # type: ignore[no-untyped-def]
    winner = (
        DeclarationOutcome.CANCELLED
        if match.status == MatchStatus.CANCELLED
        else (DeclarationOutcome(official.outcome.value) if official else None)
    )
    state = (
        decl.status
        if decl
        else (
            DeclarationStatus.FINALIZED
            if official
            else (DeclarationStatus.CANCELLED if match.status == MatchStatus.CANCELLED else DeclarationStatus.PENDING)
        )
    )
    # Original declaration metadata is retained separately from approval/revisions.
    d = declaration_out(decl, names) if decl else None
    return MatchRecord(
        match=card,
        event_name=event_name,
        workflow_status=state,
        official_winner=winner,
        declaration=d,
        declared_by=d.declared_by if d else names.get(official.declared_by_id) if official else None,
        declared_at=d.declared_at if d else official.declared_at if official else None,
        approved_by=d.approved_by if d else None,
        finalized_at=d.finalized_at if d else (match.ended_at if winner else None),
    )


def monitor_match(card: list[Match]) -> Match | None:
    active = next((m for m in card if m.status == MatchStatus.IN_PROGRESS), None)
    if active:
        return active
    settled = [m for m in card if m.status in (MatchStatus.COMPLETED, MatchStatus.CANCELLED)]
    if settled:
        return max(settled, key=lambda m: m.match_number)
    return min(card, key=lambda m: m.match_number) if card else None


async def monitor(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID) -> MonitorOut:
    event = await access_event(db, actor, event_id)
    card = await matches.list_for_event(db, actor.tenant.id, event.id)
    current = monitor_match(card)
    [event_out] = await events.to_out(db, actor.tenant.id, [event])
    return MonitorOut(
        event=event_out,
        matches=await matches.to_out(db, actor.tenant.id, card),
        current=await record(db, actor, current, event=event) if current else None,
        books=(await betting_reports.fight_summaries(db, actor.tenant.id, event_id=event.id))[0],
        betting=await betting_control(db, actor.tenant.id, event, card),
    )


# ---- betting control ------------------------------------------------------------------------
#
# The Declarator opens and closes simulated betting on the event's current fight. State lives in
# the fight's book (``fight_books``) and is enforced by ``betting.place_bet``, so a stale browser
# cannot bet on a closed fight. Opening the next scheduled fight starts it (fight numbers only
# advance when a result is finalized and the next fight is prepared, never on close or reopen).


def betting_target(card: list[Match]) -> Match | None:
    """The fight the betting switch acts on: the fight in progress, else the next scheduled fight."""
    active = next((m for m in card if m.status == MatchStatus.IN_PROGRESS), None)
    if active:
        return active
    scheduled = [m for m in card if m.status == MatchStatus.SCHEDULED]
    return min(scheduled, key=lambda m: m.match_number) if scheduled else None


def next_scheduled(card: list[Match], target: Match | None) -> Match | None:
    later = [
        m
        for m in card
        if m.status == MatchStatus.SCHEDULED and (target is None or (m.id != target.id and m.match_number > target.match_number))
    ]
    return min(later, key=lambda m: m.match_number) if later else None


async def betting_control(db: AsyncSession, tenant_id: uuid.UUID, event: Event, card: list[Match]) -> BettingControl:
    target = betting_target(card)
    following = next_scheduled(card, target)
    if target is None:
        return BettingControl(
            match_id=None,
            match_number=None,
            match_status=None,
            status="closed",
            book_state=None,
            can_open=False,
            can_close=False,
            blocked_reason="No fight is ready. The next fight is prepared when the current result is finalized.",
            next_match_number=None,
        )
    settings = await betting.settings_for(db, tenant_id)
    book = await betting.book_for(db, tenant_id, target.id)
    window = await betting.window_open(db, tenant_id, target.id)
    is_open = book is not None and book.state == BookState.OPEN
    elsewhere = [n for mid, n in await betting.open_books_in_event(db, tenant_id, event.id) if mid != target.id]
    reason = None
    if not settings.enabled:
        reason = "Simulated betting is turned off in the betting settings."
    elif event.status != EventStatus.LIVE:
        reason = "Betting opens when the event is live."
    elif not window:
        reason = f"Fight #{target.match_number} has a result awaiting approval."
    elif elsewhere:
        reason = f"Betting is still open on Fight #{elsewhere[0]}. Close it first."
    return BettingControl(
        match_id=target.id,
        match_number=target.match_number,
        match_status=target.status.value,
        status="open" if is_open and window and settings.enabled and event.status == EventStatus.LIVE else "closed",
        book_state=book.state if book else None,
        can_open=reason is None and not is_open,
        can_close=is_open,
        blocked_reason=None if is_open else reason,
        next_match_number=following.match_number if following else None,
    )


async def _betting_desk(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID) -> tuple[Event, Match]:
    """Lock the event, then the fight (the order every result path uses) and check the assignment."""
    if actor.user.account_type != AccountType.DECLARATOR or not actor.has(P.RESULTS_SUBMIT):
        raise Forbidden("Only an assigned Declarator may control betting", code="declarator_only")
    candidate = await matches.get_match(db, actor.tenant.id, match_id)
    event = await access_event(db, actor, candidate.event_id, lock=True)
    match = await matches.get_match(db, actor.tenant.id, match_id, for_update=True)
    await assigned_user(db, actor.tenant.id, event.id, actor.id)
    return event, match


async def open_betting(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, meta: RequestMeta) -> BettingControl:
    """Open (or reopen) simulated betting on the event's current fight. Repeating it changes nothing."""
    tenant_id = actor.tenant.id
    event, match = await _betting_desk(db, actor, match_id)
    if not (await betting.settings_for(db, tenant_id)).enabled:
        raise Conflict("Simulated betting is turned off in the betting settings", code="betting_disabled")
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Betting opens when the event is live", code="event_not_live")
    if match.status in (MatchStatus.COMPLETED, MatchStatus.CANCELLED) or await results.current_result(db, tenant_id, match.id):
        raise Conflict(
            f"Fight #{match.match_number} already has an official result; betting cannot reopen", code="result_finalized"
        )
    pending = (
        await db.execute(
            select(ResultDeclaration.id).where(
                ResultDeclaration.tenant_id == tenant_id,
                ResultDeclaration.match_id == match.id,
                ResultDeclaration.status.in_(ACTIVE_STATUSES),
            )
        )
    ).first()
    if pending:
        raise Conflict(f"Fight #{match.match_number} has a result awaiting approval", code="declaration_pending")
    card = await matches.list_for_event(db, tenant_id, event.id)
    target = betting_target(card)
    if target is None or target.id != match.id:
        current = f"Fight #{target.match_number}" if target else "No fight"
        raise Conflict(f"{current} is the current fight; betting opens on the current fight only", code="not_current_fight")
    elsewhere = [n for mid, n in await betting.open_books_in_event(db, tenant_id, event.id) if mid != match.id]
    if elsewhere:
        raise Conflict(f"Betting is still open on Fight #{elsewhere[0]}. Close it first.", code="betting_open_elsewhere")
    if match.status == MatchStatus.SCHEDULED:
        await matches.start_match(db, actor, match.id, meta)
    await betting.open_betting(db, actor, match.id, meta, by="declarator")
    return await betting_control(db, tenant_id, event, await matches.list_for_event(db, tenant_id, event.id))


async def close_betting(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, meta: RequestMeta) -> BettingControl:
    """Stop accepting new simulated bets on a fight. Pools, bets and odds stay; repeating it changes nothing."""
    tenant_id = actor.tenant.id
    event, match = await _betting_desk(db, actor, match_id)
    book = await betting.book_for(db, tenant_id, match.id)
    if book is not None and book.state == BookState.OPEN:
        await betting.close_betting(db, actor, match.id, meta, by="declarator")
    return await betting_control(db, tenant_id, event, await matches.list_for_event(db, tenant_id, event.id))


async def _declarator_desk_event(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID) -> Event:
    if actor.user.account_type != AccountType.DECLARATOR or not actor.has(P.RESULTS_SUBMIT):
        raise Forbidden("Only an assigned Declarator may prepare fights", code="declarator_only")
    event = await access_event(db, actor, event_id, lock=True)
    await assigned_user(db, actor.tenant.id, event.id, actor.id)
    return event


async def prepare_fight(
    db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, data: FightDetails, meta: RequestMeta
) -> Match:
    """Add the next sequential fight with the entries the Declarator supplies.

    Only when the live card has no fight in progress or scheduled (normally the progression has
    already prepared one); fight numbers always continue from the highest on the card."""
    event = await _declarator_desk_event(db, actor, event_id)
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Fights are prepared on a live event", code="event_not_live")
    card = await matches.list_for_event(db, actor.tenant.id, event.id)
    if betting_target(card) is not None:
        raise Conflict("The next fight is already prepared", code="fight_already_prepared")
    created = await matches.create_match(db, actor, event.id, MatchCreate(**data.model_dump()), meta)
    await audit.record(
        db,
        actor=actor.actor,
        action="match.next_prepared",
        resource_type="match",
        resource_id=created.id,
        tenant_id=actor.tenant.id,
        changes={"event_id": event.id, "match_number": created.match_number, "by": "declarator"},
        meta=meta,
    )
    return created


async def update_fight(
    db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: FightDetailsUpdate, meta: RequestMeta
) -> Match:
    """Enter or correct the entries of the prepared fight before betting ever opens on it."""
    event, match = await _betting_desk(db, actor, match_id)
    target = betting_target(await matches.list_for_event(db, actor.tenant.id, event.id))
    if target is None or target.id != match.id or match.status != MatchStatus.SCHEDULED:
        raise Conflict("Only the prepared next fight can be edited", code="not_current_fight")
    if await betting.book_for(db, actor.tenant.id, match.id) is not None:
        raise Conflict("Betting has opened on this fight; its entries are locked", code="match_locked")
    return await matches.update_match(db, actor, match.id, MatchUpdate(**data.model_dump(exclude_unset=True)), meta)


async def prepare_next_fight(db: AsyncSession, actor: CurrentUser, event: Event, match: Match, meta: RequestMeta) -> None:
    """After a fight's first official result, make sure the live card has a next fight to open.

    Uses an already scheduled fight when there is one; otherwise adds the next number. The caller
    holds the event lock, which also serialises fight numbering."""
    if event.status != EventStatus.LIVE:
        return
    waiting = (
        await db.execute(
            select(Match.id).where(
                Match.tenant_id == actor.tenant.id,
                Match.event_id == event.id,
                Match.status == MatchStatus.SCHEDULED,
                Match.deleted_at.is_(None),
            )
        )
    ).first()
    if waiting:
        return
    created = await matches.create_match(db, actor, event.id, MatchCreate(meron_name="Meron", wala_name="Wala"), meta)
    await audit.record(
        db,
        actor=actor.actor,
        action="match.next_prepared",
        resource_type="match",
        resource_id=created.id,
        tenant_id=actor.tenant.id,
        changes={"event_id": event.id, "after_match_number": match.match_number, "match_number": created.match_number},
        meta=meta,
    )


async def records(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    event_id: uuid.UUID | None = None,
    winner: DeclarationOutcome | None = None,
    status: DeclarationStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[MatchRecord]:
    if date_from and date_to and date_from > date_to:
        raise Unprocessable("Start date must be before the end date")
    latest_id = (
        select(ResultDeclaration.id)
        .where(
            ResultDeclaration.match_id == Match.id,
            ResultDeclaration.tenant_id == actor.tenant.id,
        )
        .order_by(ResultDeclaration.created_at.desc(), ResultDeclaration.id.desc())
        .limit(1)
        .correlate(Match)
        .scalar_subquery()
    )
    stmt = (
        select(Match)
        .join(Event, Event.id == Match.event_id)
        .outerjoin(
            MatchResult,
            MatchResult.match_id == Match.id,
        )
        .outerjoin(ResultDeclaration, ResultDeclaration.id == latest_id)
        .where(
            Match.tenant_id == actor.tenant.id,
            Match.deleted_at.is_(None),
            Event.tenant_id == actor.tenant.id,
            Event.deleted_at.is_(None),
        )
    )
    if not is_approver(actor):
        if actor.user.account_type != AccountType.DECLARATOR:
            raise Forbidden("Declarator access required")
        stmt = stmt.where(assignment_filter(actor), Event.status.in_(VISIBLE_EVENT_STATUSES))
    if event_id:
        await access_event(db, actor, event_id)
        stmt = stmt.where(Match.event_id == event_id)
    if params.q:
        if not params.q.isascii() or not params.q.isdigit() or len(params.q) > 9:
            return Page(items=[], total=0, page=params.page, page_size=params.page_size)
        stmt = stmt.where(Match.match_number == int(params.q))
    official_winner = case((Match.status == MatchStatus.CANCELLED, "cancelled"), else_=cast(MatchResult.outcome, String))
    workflow_status = case(
        (ResultDeclaration.id.is_not(None), cast(ResultDeclaration.status, String)),
        (MatchResult.id.is_not(None), "finalized"),
        (Match.status == MatchStatus.CANCELLED, "cancelled"),
        else_="pending",
    )
    if winner:
        stmt = stmt.where(official_winner == winner.value)
    if status:
        stmt = stmt.where(workflow_status == status.value)
    # UTC activity window; undelared records use scheduled event date.
    activity_date = func.coalesce(ResultDeclaration.created_at, MatchResult.declared_at, Match.started_at, Event.starts_at)
    if date_from:
        stmt = stmt.where(activity_date >= datetime.combine(date_from, time.min, UTC))
    if date_to and date_to < date.max:
        stmt = stmt.where(activity_date < datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    rows, total = await paginate(db, stmt.order_by(Event.starts_at.desc(), Match.match_number.desc(), Match.id), params)
    # Batch page projections instead of querying each record independently.
    names = await usernames(db, actor.tenant.id)
    ids = [m.id for m in rows]
    cards = {m.id: m for m in await matches.to_out(db, actor.tenant.id, rows)}
    declarations = {
        d.match_id: d
        for d in (
            await db.execute(
                select(ResultDeclaration)
                .where(
                    ResultDeclaration.tenant_id == actor.tenant.id,
                    ResultDeclaration.match_id.in_(ids),
                )
                .distinct(ResultDeclaration.match_id)
                .order_by(ResultDeclaration.match_id, ResultDeclaration.created_at.desc(), ResultDeclaration.id.desc())
            )
        ).scalars()
    }
    officials = {
        r.match_id: r
        for r in (
            await db.execute(
                select(MatchResult).where(
                    MatchResult.tenant_id == actor.tenant.id,
                    MatchResult.match_id.in_(ids),
                )
            )
        ).scalars()
    }
    event_names = dict(
        (
            await db.execute(
                select(Event.id, Event.name).where(
                    Event.tenant_id == actor.tenant.id,
                    Event.id.in_({m.event_id for m in rows}),
                )
            )
        )
        .tuples()
        .all()
    )
    return Page(
        items=[
            format_record(m, cards[m.id], declarations.get(m.id), officials.get(m.id), event_names[m.event_id], names)
            for m in rows
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


async def history(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID) -> MatchHistory:
    match = await matches.get_match(db, actor.tenant.id, match_id)
    await access_event(db, actor, match.event_id)
    names = await usernames(db, actor.tenant.id)
    declarations = list(
        (
            await db.execute(
                select(ResultDeclaration)
                .where(
                    ResultDeclaration.tenant_id == actor.tenant.id,
                    ResultDeclaration.match_id == match.id,
                )
                .order_by(ResultDeclaration.created_at, ResultDeclaration.id)
            )
        ).scalars()
    )
    audits = list(
        (
            await db.execute(
                select(DeclarationAudit)
                .where(
                    DeclarationAudit.tenant_id == actor.tenant.id,
                    DeclarationAudit.declaration_id.in_([d.id for d in declarations]),
                )
                .order_by(DeclarationAudit.created_at, DeclarationAudit.sequence, DeclarationAudit.id)
            )
        ).scalars()
    )
    revisions = []
    for r in await results.history(db, actor.tenant.id, match.id):
        item = ResultHistoryOut.model_validate(r)
        item.changed_by_username = names.get(r.changed_by_id)
        revisions.append(item)
    return MatchHistory(
        record=await record(db, actor, match, names=names),
        declarations=[declaration_out(d, names) for d in declarations],
        audit=[
            AuditOut(
                id=a.id,
                declaration_id=a.declaration_id,
                actor=names.get(a.actor_id, "Unavailable account"),
                action=a.action,
                from_status=a.from_status,
                to_status=a.to_status,
                reason=a.reason,
                created_at=a.created_at,
            )
            for a in audits
        ],
        revisions=revisions,
    )


async def assigned_user(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    user = await get_scoped(db, TenantUser, user_id, tenant_id)
    assignment = (
        await db.execute(
            select(EventDeclarator.id).where(
                EventDeclarator.tenant_id == tenant_id,
                EventDeclarator.event_id == event_id,
                EventDeclarator.user_id == user_id,
            )
        )
    ).first()
    if not user or user.account_type != AccountType.DECLARATOR or user.status != AccountStatus.ACTIVE or not assignment:
        raise Forbidden("An active Declarator assigned to this event is required", code="assignment_required")
    return user


async def audience(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID) -> set[uuid.UUID]:
    approvers = (
        select(UserRole.user_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .where(
            UserRole.tenant_id == tenant_id, RolePermission.tenant_id == tenant_id, RolePermission.permission == P.RESULTS_APPROVE
        )
    )
    assigned = (
        select(EventDeclarator.user_id)
        .join(UserRole, UserRole.user_id == EventDeclarator.user_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .where(
            EventDeclarator.tenant_id == tenant_id,
            EventDeclarator.event_id == event_id,
            UserRole.tenant_id == tenant_id,
            RolePermission.tenant_id == tenant_id,
            RolePermission.permission == P.RESULTS_SUBMIT,
        )
    )
    return set(
        (
            await db.execute(
                select(TenantUser.id).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.deleted_at.is_(None),
                    TenantUser.status == AccountStatus.ACTIVE,
                    TenantUser.id.in_(approvers.union(assigned)),
                )
            )
        ).scalars()
    )


async def transition_audit(
    db: AsyncSession,
    actor: CurrentUser,
    decl: ResultDeclaration,
    target: DeclarationStatus,
    action: str,
    reason: str | None,
    meta: RequestMeta,
) -> None:
    previous = decl.status
    decl.status = target
    step = (
        await db.execute(
            select(func.coalesce(func.max(DeclarationAudit.sequence), 0)).where(
                DeclarationAudit.tenant_id == actor.tenant.id,
                DeclarationAudit.declaration_id == decl.id,
            )
        )
    ).scalar_one() + 1
    db.add(
        DeclarationAudit(
            tenant_id=actor.tenant.id,
            declaration_id=decl.id,
            actor_id=actor.id,
            sequence=step,
            action=action,
            from_status=previous.value,
            to_status=target.value,
            reason=reason,
        )
    )
    await audit.record(
        db,
        actor=actor.actor,
        action=f"declaration.{action}",
        resource_type="match",
        resource_id=decl.match_id,
        tenant_id=actor.tenant.id,
        changes={
            "declaration_id": decl.id,
            "status": [previous, target],
            "outcome": decl.outcome,
            "base_revision": decl.base_revision,
            "reason": reason,
        },
        meta=meta,
    )


async def announce(db: AsyncSession, actor: CurrentUser, match: Match, decl: ResultDeclaration, *, submitted=False) -> None:
    for uid in await audience(db, actor.tenant.id, match.event_id):
        publish_after_commit(
            db,
            actor.tenant.id,
            "declaration.updated",
            {"id": decl.id, "match_id": match.id, "event_id": match.event_id, "status": decl.status},
            user_id=uid,
        )
        permission = (
            (
                await db.execute(
                    select(RolePermission.permission)
                    .join(UserRole, UserRole.role_id == RolePermission.role_id)
                    .where(
                        UserRole.tenant_id == actor.tenant.id,
                        UserRole.user_id == uid,
                        RolePermission.permission == P.RESULTS_APPROVE,
                    )
                    .limit(1)
                )
            ).first()
            if submitted
            else None
        )
        if submitted and uid != actor.id and permission:
            await notify(
                db,
                tenant_id=actor.tenant.id,
                user_id=uid,
                type="result.approval_required",
                title=f"Fight #{match.match_number} awaits result approval",
                body=f"Proposed outcome: {decl.outcome.value.replace('_', ' ')}",
                data={"declaration_id": str(decl.id), "event_id": str(match.event_id)},
            )


async def submit(
    db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: DeclarationCreate, meta: RequestMeta
) -> ResultDeclaration:
    if actor.user.account_type != AccountType.DECLARATOR or not actor.has(P.RESULTS_SUBMIT):
        raise Forbidden("Only an assigned Declarator may submit a declaration")
    m = await matches.get_match(db, actor.tenant.id, match_id)
    event = await access_event(db, actor, m.event_id, lock=True)
    match = await matches.get_match(db, actor.tenant.id, match_id, for_update=True)
    await assigned_user(db, actor.tenant.id, event.id, actor.id)
    old = (
        await db.execute(
            select(ResultDeclaration).where(
                ResultDeclaration.tenant_id == actor.tenant.id,
                ResultDeclaration.request_id == data.request_id,
            )
        )
    ).scalar_one_or_none()
    if old:
        if (old.match_id, old.declared_by_id, old.outcome, old.base_revision, old.reason) != (
            match.id,
            actor.id,
            data.outcome,
            data.base_revision,
            data.reason,
        ):
            raise Conflict("This request key was used for a different declaration", code="idempotency_conflict")
        return old
    active = (
        await db.execute(
            select(ResultDeclaration.id).where(
                ResultDeclaration.tenant_id == actor.tenant.id,
                ResultDeclaration.match_id == match.id,
                ResultDeclaration.status.in_(ACTIVE_STATUSES),
            )
        )
    ).first()
    if active:
        raise Conflict("A declaration is already awaiting verification", code="declaration_exists")
    official = await results.current_result(db, actor.tenant.id, match.id, for_update=True)
    validate_proposal(event, match, official, data)
    decl = ResultDeclaration(
        tenant_id=actor.tenant.id,
        match_id=match.id,
        request_id=data.request_id,
        outcome=data.outcome,
        status=DeclarationStatus.DECLARED,
        base_revision=data.base_revision,
        reason=data.reason,
        declared_by_id=actor.id,
    )
    db.add(decl)
    await db.flush()
    db.add(
        DeclarationAudit(
            tenant_id=actor.tenant.id,
            declaration_id=decl.id,
            actor_id=actor.id,
            sequence=1,
            action="declared",
            from_status=None,
            to_status="declared",
            reason=data.reason,
        )
    )
    await transition_audit(db, actor, decl, DeclarationStatus.PENDING_APPROVAL, "submitted", data.reason, meta)
    await db.flush()
    from app.modules.betting.service import close_for_declaration

    await close_for_declaration(db, actor, match, meta)
    await announce(db, actor, match, decl, submitted=True)
    return decl


def validate_proposal(event: Event, match: Match, official: MatchResult | None, data: DeclarationCreate) -> None:
    revision = official.revision if official else 0
    if revision != data.base_revision:
        raise Conflict("The official result changed. Reload before declaring.", code="stale_revision")
    if data.base_revision:
        if (
            event.status not in (EventStatus.LIVE, EventStatus.COMPLETED)
            or match.status != MatchStatus.COMPLETED
            or data.outcome == DeclarationOutcome.CANCELLED
        ):
            raise Unprocessable("This fight cannot receive an amendment", code="invalid_match_state")
        if official and official.outcome.value == data.outcome.value:
            raise Unprocessable("The proposed result is unchanged", code="no_change")
    elif event.status != EventStatus.LIVE or match.status != MatchStatus.IN_PROGRESS:
        raise Unprocessable("A live event and a fight in progress are required", code="invalid_match_state")


async def review(
    db: AsyncSession, actor: CurrentUser, declaration_id: uuid.UUID, action: str, reason: str | None, meta: RequestMeta
) -> ResultDeclaration:
    if not is_approver(actor):
        raise Forbidden("Result approval permission required")
    decl = await get_scoped(db, ResultDeclaration, declaration_id, actor.tenant.id)
    if not decl:
        raise NotFound("Declaration not found")
    m = await matches.get_match(db, actor.tenant.id, decl.match_id)
    event = await access_event(db, actor, m.event_id, lock=True)
    match = await matches.get_match(db, actor.tenant.id, m.id, for_update=True)
    decl = await get_scoped(db, ResultDeclaration, declaration_id, actor.tenant.id, for_update=True)
    # Refresh identity-map values after waiting for concurrent approval.
    await db.refresh(decl)
    if decl.declared_by_id == actor.id:
        raise Forbidden("A different authorized user must verify this declaration", code="self_approval")
    if decl.status == DeclarationStatus.FINALIZED and action == "approve":
        return decl
    if decl.status not in ACTIVE_STATUSES:
        raise Conflict("This declaration is already closed", code="declaration_closed")
    if action == "approve":
        declarator = await assigned_user(db, actor.tenant.id, event.id, decl.declared_by_id)
        from app.core.deps import load_permissions

        if P.RESULTS_SUBMIT not in await load_permissions(db, actor.tenant.id, declarator.id):
            raise Forbidden("The Declarator no longer has submission permission")
        official = await results.current_result(db, actor.tenant.id, match.id, for_update=True)
        data = DeclarationCreate(
            request_id=decl.request_id, outcome=decl.outcome, base_revision=decl.base_revision, reason=decl.reason
        )
        validate_proposal(event, match, official, data)
        if decl.outcome == DeclarationOutcome.CANCELLED:
            await matches.cancel_match(db, actor, match.id, decl.reason or "Cancelled after verification", meta, _workflow=True)
        else:
            outcome = MatchOutcome(decl.outcome.value)
            if decl.base_revision:
                _, official = await results.correct(
                    db, actor, match.id, outcome, decl.reason or "Amended after review", None, meta, _workflow=True
                )
            else:
                _, official = await results.declare(db, actor, match.id, outcome, decl.reason, meta, _workflow=True)
            official.declared_by_id = decl.declared_by_id
            official.declared_at = decl.created_at
        decl.approved_by_id = actor.id
        decl.finalized_at = utcnow()
        await transition_audit(db, actor, decl, DeclarationStatus.FINALIZED, "approved", reason, meta)
        if not decl.base_revision:  # amendments never advance the card
            await prepare_next_fight(db, actor, event, match, meta)
    elif action in ("review", "return"):
        if not reason or len(reason.strip()) < 3:
            raise Unprocessable("A review reason is required")
        target = DeclarationStatus.UNDER_REVIEW if action == "review" else DeclarationStatus.CANCELLED
        await transition_audit(db, actor, decl, target, "reviewed" if action == "review" else "returned", reason, meta)
    else:
        raise Unprocessable("Unknown review action")
    await db.flush()
    await announce(db, actor, match, decl)
    return decl


async def assignments(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID) -> list[AssignmentOut]:
    await events.get_event(db, actor.tenant.id, event_id)
    rows = (
        await db.execute(
            select(EventDeclarator, TenantUser.username)
            .join(TenantUser, TenantUser.id == EventDeclarator.user_id)
            .where(
                EventDeclarator.tenant_id == actor.tenant.id,
                EventDeclarator.event_id == event_id,
            )
            .order_by(TenantUser.username)
        )
    ).all()
    return [AssignmentOut(user_id=a.user_id, username=name, assigned_at=a.created_at) for a, name in rows]


async def assign(
    db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, user_id: uuid.UUID, remove: bool, meta: RequestMeta
) -> None:
    event = await get_scoped(db, Event, event_id, actor.tenant.id, for_update=True)
    if not event:
        raise NotFound("Event not found")
    row = (
        await db.execute(
            select(EventDeclarator).where(
                EventDeclarator.tenant_id == actor.tenant.id,
                EventDeclarator.event_id == event_id,
                EventDeclarator.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if remove:
        if row:
            await db.delete(row)
    else:
        user = await get_scoped(db, TenantUser, user_id, actor.tenant.id)
        if not user or user.account_type != AccountType.DECLARATOR or user.status != AccountStatus.ACTIVE:
            raise Unprocessable("Select an active Declarator in this workspace")
        event.result_verification_required = True
        if not row:
            db.add(EventDeclarator(tenant_id=actor.tenant.id, event_id=event_id, user_id=user_id, assigned_by_id=actor.id))
    await audit.record(
        db,
        actor=actor.actor,
        action="declarator.unassigned" if remove else "declarator.assigned",
        resource_type="event",
        resource_id=event_id,
        tenant_id=actor.tenant.id,
        changes={"user_id": user_id},
        meta=meta,
    )
    publish_after_commit(db, actor.tenant.id, "declaration.assignment_changed", {"event_id": event_id}, user_id=user_id)
