import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, Meta, TenantDb, require_any_permission, require_module, require_permissions
from app.core.permissions import P
from app.modules.declarator import service
from app.modules.declarator.models import DeclarationOutcome, DeclarationStatus
from app.modules.declarator.schemas import (
    AssignmentOut,
    DeclarationCreate,
    DeclarationOut,
    EligibleDeclarator,
    MatchHistory,
    MatchRecord,
    MonitorOut,
    ReviewRequest,
)
from app.modules.events.schemas import EventOut
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
