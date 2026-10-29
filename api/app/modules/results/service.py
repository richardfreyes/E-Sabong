"""Official match results.

Declaring a result records the outcome of a match for display and history. It
does not move points or settle anything: the points ledger is independent of
matches by design.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.declarator.models import ResultDeclaration
from app.modules.events.models import Event
from app.modules.matches.models import Match, MatchStatus
from app.modules.matches.service import get_match, realtime_payload
from app.modules.predictions import service as predictions
from app.modules.realtime.publisher import publish_after_commit
from app.modules.results.models import MatchOutcome, MatchResult, MatchResultHistory
from app.shared.repository import get_scoped


async def current_result(
    db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID, *, for_update: bool = False
) -> MatchResult | None:
    stmt = select(MatchResult).where(MatchResult.tenant_id == tenant_id, MatchResult.match_id == match_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def declare(
    db: AsyncSession,
    actor: CurrentUser,
    match_id: uuid.UUID,
    outcome: MatchOutcome,
    notes: str | None,
    meta: RequestMeta,
    *,
    _workflow: bool = False,
) -> tuple[Match, MatchResult]:
    tenant_id = actor.tenant.id
    match = await get_match(db, tenant_id, match_id)
    await get_scoped(db, Event, match.event_id, tenant_id, for_update=True)
    match = await get_match(db, tenant_id, match_id, for_update=True)
    if not _workflow:
        await assert_unmanaged(db, tenant_id, match.id)
    if match.status != MatchStatus.IN_PROGRESS:
        raise Unprocessable(
            "Results can only be declared for a match in progress",
            code="invalid_match_state",
            details={"status": match.status.value},
        )
    if await current_result(db, tenant_id, match.id) is not None:
        raise Conflict("A result has already been declared; use a correction", code="result_exists")
    now = utcnow()
    result = MatchResult(
        tenant_id=tenant_id,
        match_id=match.id,
        outcome=outcome,
        notes=notes,
        declared_by_id=actor.id,
        declared_at=now,
        revision=1,
    )
    db.add(result)
    db.add(
        MatchResultHistory(
            tenant_id=tenant_id,
            match_id=match.id,
            revision=1,
            outcome=outcome,
            previous_outcome=None,
            reason=notes,
            changed_by_id=actor.id,
        )
    )
    match.status = MatchStatus.COMPLETED
    match.ended_at = now
    await db.flush()
    await db.refresh(match)
    await predictions.resolve(db, tenant_id, match_ids=[match.id])
    await audit.record(
        db,
        actor=actor.actor,
        action="result.declared",
        resource_type="match",
        resource_id=match.id,
        tenant_id=tenant_id,
        changes={"outcome": outcome, "notes": notes},
        meta=meta,
    )
    publish_after_commit(db, tenant_id, "match.result_declared", realtime_payload(match, result))
    return match, result


async def correct(
    db: AsyncSession,
    actor: CurrentUser,
    match_id: uuid.UUID,
    outcome: MatchOutcome,
    reason: str,
    notes: str | None,
    meta: RequestMeta,
    *,
    _workflow: bool = False,
) -> tuple[Match, MatchResult]:
    tenant_id = actor.tenant.id
    match = await get_match(db, tenant_id, match_id)
    await get_scoped(db, Event, match.event_id, tenant_id, for_update=True)
    match = await get_match(db, tenant_id, match_id, for_update=True)
    if not _workflow:
        await assert_unmanaged(db, tenant_id, match.id)
    result = await current_result(db, tenant_id, match.id, for_update=True)
    if result is None:
        raise NotFound("No result has been declared for this match", code="result_missing")
    if result.outcome == outcome:
        raise Unprocessable("The corrected outcome is the same as the current one", code="no_change")
    previous = result.outcome
    result.outcome = outcome
    result.revision += 1
    result.notes = notes if notes is not None else result.notes
    result.declared_by_id = actor.id
    result.declared_at = utcnow()
    db.add(
        MatchResultHistory(
            tenant_id=tenant_id,
            match_id=match.id,
            revision=result.revision,
            outcome=outcome,
            previous_outcome=previous,
            reason=reason,
            changed_by_id=actor.id,
        )
    )
    await db.flush()
    await predictions.resolve(db, tenant_id, match_ids=[match.id])
    await audit.record(
        db,
        actor=actor.actor,
        action="result.corrected",
        resource_type="match",
        resource_id=match.id,
        tenant_id=tenant_id,
        changes={"outcome": [previous, outcome], "reason": reason, "revision": result.revision},
        meta=meta,
    )
    publish_after_commit(db, tenant_id, "match.result_corrected", realtime_payload(match, result))
    return match, result


async def history(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> list[MatchResultHistory]:
    rows = await db.execute(
        select(MatchResultHistory)
        .where(MatchResultHistory.tenant_id == tenant_id, MatchResultHistory.match_id == match_id)
        .order_by(MatchResultHistory.revision)
    )
    return list(rows.scalars())


async def assert_unmanaged(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> None:
    declared = (
        select(ResultDeclaration.id)
        .where(ResultDeclaration.tenant_id == tenant_id, ResultDeclaration.match_id == match_id)
        .exists()
    )
    assigned = (
        select(Event.id)
        .join(Match, Match.event_id == Event.id)
        .where(
            Event.tenant_id == tenant_id,
            Event.result_verification_required.is_(True),
            Match.tenant_id == tenant_id,
            Match.id == match_id,
            Match.status.in_([MatchStatus.IN_PROGRESS, MatchStatus.COMPLETED]),
        )
        .exists()
    )
    managed = (await db.execute(select(or_(declared, assigned)))).scalar_one()
    if managed:
        raise Conflict(
            "This fight uses declaration verification; submit or approve an amendment in the Declarator workspace",
            code="verification_required",
        )
