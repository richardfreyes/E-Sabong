"""OWNER rental controls reuse the existing subscription and video services."""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import Meta, PlatformDb, PlatformSuperAdmin
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.security import utcnow
from app.core.tenancy import TenantContext
from app.modules.audit import service as audit
from app.modules.events.models import Event, EventStatus
from app.modules.events.schemas import EventCreate, MatchCreate, ResultCorrect, ResultDeclare
from app.modules.subscriptions import service as billing
from app.modules.subscriptions.models import BillingCycle, SubscriptionPlan
from app.modules.tenants.models import ALL_MODULES, Tenant
from app.modules.tenants.rental import RentalSettings
from app.modules.users.models import AccountType, TenantUser
from app.modules.users.schemas import UserOut
from app.modules.video.models import VideoStream
from app.modules.video.stream_schemas import StreamCreate, StreamUpdate
from app.shared.schemas import Page, PageDep, Schema, paginate

router = APIRouter(prefix="/platform/rental", tags=["platform: rental"])


class RentalConfig(Schema):
    shared_domain_enabled: bool = False
    default_tenant_id: uuid.UUID | None = None
    broadcast_tenant_id: uuid.UUID | None = None
    global_event_id: uuid.UUID | None = None
    global_stream_id: uuid.UUID | None = None


class RentalConfigSaved(RentalConfig):
    obs_stream_key: str | None = None


class RentalPrice(Schema):
    monthly_cents: int = Field(ge=0, le=100_000_000_000)
    next_billing_at: datetime | None = None
    activation_at: datetime | None = None

    @model_validator(mode="after")
    def dated(self):
        if self.activation_at and self.activation_at.tzinfo is None:
            raise ValueError("Activation date must include a timezone")
        if self.next_billing_at and (self.next_billing_at.tzinfo is None or self.next_billing_at <= utcnow()):
            raise ValueError("Next billing date must be a future timestamp with timezone")
        return self


async def control_actor(db, owner, tenant_id):
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None
    if not tenant or tenant.deleted_at:
        raise Unprocessable("Select the global broadcast workspace first", code="broadcast_workspace_required")
    return SimpleNamespace(
        id=owner.id,
        actor=owner.actor,
        tenant=TenantContext(tenant.id, tenant.slug, tenant.name, tenant.status),
        has=lambda *args: True,
        has_any=lambda *args: True,
    )


@router.get("/settings", response_model=RentalConfig)
async def get_settings_config(_: PlatformSuperAdmin, db: PlatformDb):
    return await db.get(RentalSettings, 1) or RentalConfig()


@router.put("/settings", response_model=RentalConfigSaved)
async def save_settings(body: RentalConfig, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    for tid in (body.default_tenant_id, body.broadcast_tenant_id):
        tenant = await db.get(Tenant, tid) if tid else None
        if tid and (tenant is None or tenant.deleted_at):
            raise NotFound("Workspace not found")
    if body.shared_domain_enabled and not body.broadcast_tenant_id:
        raise Unprocessable("Select the OWNER-controlled broadcast workspace")
    for model, rid in ((Event, body.global_event_id), (VideoStream, body.global_stream_id)):
        row = await db.get(model, rid) if rid else None
        if rid and (row is None or row.tenant_id != body.broadcast_tenant_id):
            raise Unprocessable("The global event and stream must belong to the broadcast workspace")
    row = await db.get(RentalSettings, 1, with_for_update=True)
    stream_changed = (
        row is None
        or row.global_stream_id != body.global_stream_id
        or row.broadcast_tenant_id != body.broadcast_tenant_id
        or row.shared_domain_enabled != body.shared_domain_enabled
    )
    if body.shared_domain_enabled and (row is None or not row.shared_domain_enabled):
        from app.modules.betting.models import BookState, FightBook
        from app.modules.matches.models import Match

        pending = (
            await db.execute(
                select(FightBook.tenant_id, Match.event_id)
                .join(Match, Match.id == FightBook.match_id)
                .where(FightBook.state != BookState.SETTLED)
            )
        ).all()
        for tid, eid in pending:
            expected = (
                body.global_event_id
                if tid == body.broadcast_tenant_id
                else (uuid.uuid5(tid, str(body.global_event_id)) if body.global_event_id else None)
            )
            if eid != expected:
                raise Conflict("Settle existing tenant fights before enabling the shared arena", code="legacy_bets_unsettled")
    if (
        row
        and row.shared_domain_enabled
        and row.global_event_id
        and (row.global_event_id != body.global_event_id or row.broadcast_tenant_id != body.broadcast_tenant_id)
    ):
        from app.modules.betting.models import BookState, FightBook
        from app.modules.matches.models import Match

        unsettled = await db.scalar(
            select(FightBook.id)
            .join(Match, Match.id == FightBook.match_id)
            .where(
                Match.event_id == row.global_event_id,
                FightBook.state != BookState.SETTLED,
            )
            .limit(1)
        )
        if unsettled:
            raise Conflict(
                "Settle or cancel the current official fights before switching the global event", code="unsettled_global_event"
            )
    if row is None:
        row = RentalSettings(id=1)
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await audit.record(
        db,
        actor=current.actor,
        action="rental.settings_updated",
        resource_type="rental_settings",
        resource_id="1",
        changes=body.model_dump(mode="json"),
        meta=meta,
    )
    await db.flush()
    key = None
    if row.shared_domain_enabled and row.global_stream_id:
        from app.modules.audit.models import ActorType, AuditLog

        stream = await db.get(VideoStream, row.global_stream_id)
        if stream and stream.source_kind == "obs":
            last_author = await db.scalar(
                select(AuditLog.actor_type)
                .where(
                    AuditLog.resource_id == str(stream.id),
                    AuditLog.action.in_(["video.stream_created", "video.stream_key_rotated"]),
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(1)
            )
            if last_author != ActorType.PLATFORM_USER:
                # Transfer legacy ingest control to the OWNER without retaining a tenant's key.
                from app.modules.video.stream_router import rotate_key

                actor = await control_actor(db, current, row.broadcast_tenant_id)
                key = (await rotate_key(stream.id, actor, db, meta)).obs_stream_key
    await sync_arena(db, row, current, meta)
    if row.shared_domain_enabled and stream_changed:
        from app.modules.realtime.publisher import publish_after_commit

        publish_after_commit(
            db,
            row.broadcast_tenant_id,
            "video.source_changed",
            {
                "stream_id": str(row.global_stream_id) if row.global_stream_id else None,
            },
        )
    return RentalConfigSaved(**RentalConfig.model_validate(row).model_dump(), obs_stream_key=key)


@router.put("/tenants/{tenant_id}/price")
async def set_rental(tenant_id: uuid.UUID, body: RentalPrice, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    tenant = await db.get(Tenant, tenant_id, with_for_update=True)
    if tenant is None or tenant.deleted_at:
        raise NotFound("Tenant not found")
    sub = await billing.get_live(db, tenant_id)
    code = f"rental-{tenant_id.hex}"
    plan = await db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code).with_for_update())
    if plan is None:
        plan = SubscriptionPlan(
            code=code,
            name=f"{tenant.name} monthly rental",
            price_monthly_cents=body.monthly_cents,
            price_annual_cents=body.monthly_cents * 12,
            currency=get_settings().default_currency,
            trial_days=0,
            modules=list(sub.plan.modules) if sub else list(ALL_MODULES),
            limits=dict(sub.plan.limits) if sub else {},
            is_active=False,
            prices_approved_at=utcnow(),
            prices_approved_by=current.id,
        )
        db.add(plan)
        await db.flush()
    plan.price_monthly_cents = body.monthly_cents
    plan.price_annual_cents = body.monthly_cents * 12
    plan.prices_approved_at, plan.prices_approved_by = utcnow(), current.id
    if sub:
        sub.plan_id, sub.billing_cycle = plan.id, BillingCycle.MONTHLY
        sub.pending_plan_id = sub.pending_billing_cycle = None
    else:
        # Private rental plans are never offered in public checkout.
        plan.is_active = True
        sub = await billing.start_subscription(
            db, tenant_id=tenant_id, plan=plan, cycle=BillingCycle.MONTHLY, with_trial=False, actor=current.actor, meta=meta
        )
        plan.is_active = False
    if body.next_billing_at:
        if body.next_billing_at <= sub.current_period_start:
            raise Unprocessable("Next billing must be after activation")
        sub.current_period_end = body.next_billing_at
    if body.activation_at:
        if body.activation_at >= sub.current_period_end:
            raise Unprocessable("Activation must be before the next billing date")
        sub.current_period_start = body.activation_at
    await audit.record(
        db,
        actor=current.actor,
        action="rental.price_updated",
        resource_type="tenant_subscription",
        resource_id=sub.id,
        tenant_id=tenant_id,
        changes={
            "monthly_cents": body.monthly_cents,
            "next_billing_at": sub.current_period_end.isoformat(),
            "activation_at": sub.current_period_start.isoformat(),
        },
        meta=meta,
    )
    return {"monthly_cents": body.monthly_cents, "next_billing_at": sub.current_period_end}


@router.get("/tenants/{tenant_id}/summary")
async def tenant_summary(tenant_id: uuid.UUID, _: PlatformSuperAdmin, db: PlatformDb):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at:
        raise NotFound("Tenant not found")
    counts = dict(
        (
            await db.execute(
                select(TenantUser.account_type, func.count())
                .where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
                .group_by(TenantUser.account_type)
            )
        ).all()
    )
    sub = await billing.get_live(db, tenant_id)
    admin = await db.scalar(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant_id, TenantUser.account_type == AccountType.ADMIN, TenantUser.deleted_at.is_(None))
        .order_by(TenantUser.created_at)
    )
    s = get_settings()
    return {
        "tenant_id": tenant_id,
        "accounts": counts,
        "monthly_cents": sub.plan.price_monthly_cents if sub else None,
        "next_billing_at": sub.current_period_end if sub else None,
        "activation_at": sub.current_period_start if sub else None,
        "subscription_status": sub.status if sub else None,
        "player_registration_url": f"{s.public_scheme}://{s.base_domain}/register?ref={admin.id}" if admin else None,
        "admin_login": f"{admin.username}@{tenant.slug}" if admin else None,
    }


@router.get("/streams")
async def owner_streams(current: PlatformSuperAdmin, db: PlatformDb):
    from app.modules.video.stream_router import list_streams

    config = await db.get(RentalSettings, 1)
    return await list_streams(await control_actor(db, current, config.broadcast_tenant_id if config else None), db)


@router.get("/tenants/{tenant_id}/accounts", response_model=Page[UserOut])
async def tenant_accounts(tenant_id: uuid.UUID, _: PlatformSuperAdmin, db: PlatformDb, params: PageDep):
    from app.modules.users.service import to_out

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at:
        raise NotFound("Tenant not found")
    query = select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
    if params.q:
        query = query.where(TenantUser.username.ilike(f"%{params.q}%"))
    rows, total = await paginate(db, query.order_by(TenantUser.created_at, TenantUser.id), params)
    return Page(items=await to_out(db, tenant_id, rows), total=total, page=params.page, page_size=params.page_size)


@router.get("/alerts")
async def billing_alerts(_: PlatformSuperAdmin, db: PlatformDb):
    from app.modules.subscriptions.models import Invoice, InvoiceStatus

    now = utcnow()
    rows = (
        await db.execute(
            select(Invoice, Tenant.name)
            .join(Tenant, Tenant.id == Invoice.tenant_id)
            .where(
                Invoice.status == InvoiceStatus.OPEN,
                Invoice.due_at <= now + timedelta(days=7),
                Tenant.deleted_at.is_(None),
            )
            .order_by(Invoice.due_at)
            .limit(100)
        )
    ).all()
    return [
        {
            "tenant_id": inv.tenant_id,
            "tenant_name": name,
            "number": inv.number,
            "due_at": inv.due_at,
            "total_cents": inv.total_cents,
            "status": "overdue" if inv.due_at < now else "upcoming",
        }
        for inv, name in rows
    ]


@router.post("/streams/{stream_id}/rotate-key")
async def owner_rotate_key(stream_id: uuid.UUID, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.video.stream_router import rotate_key

    config = await db.get(RentalSettings, 1)
    actor = await control_actor(db, current, config.broadcast_tenant_id if config else None)
    return await rotate_key(stream_id, actor, db, meta)


@router.post("/streams", status_code=201)
async def owner_create_stream(body: StreamCreate, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.video.stream_router import create_stream

    config = await db.get(RentalSettings, 1)
    actor = await control_actor(db, current, config.broadcast_tenant_id if config else None)
    return await create_stream(body, actor, db, meta)


@router.patch("/streams/{stream_id}")
async def owner_update_stream(stream_id: uuid.UUID, body: StreamUpdate, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.video.stream_router import update_stream

    config = await db.get(RentalSettings, 1)
    actor = await control_actor(db, current, config.broadcast_tenant_id if config else None)
    return await update_stream(stream_id, body, actor, db, meta)


async def arena_context(db, owner):
    config = await db.get(RentalSettings, 1, with_for_update=True)
    actor = await control_actor(db, owner, config.broadcast_tenant_id if config else None)
    return config, actor


async def sync_arena(db, config, owner, meta):
    from app.modules.tenants.global_arena import synchronize

    await synchronize(db, config, owner, meta)


@router.post("/synchronize")
async def resync(current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    config, _ = await arena_context(db, current)
    await sync_arena(db, config, current, meta)
    return {"message": "Official arena synchronized"}


@router.get("/events")
async def owner_events(current: PlatformSuperAdmin, db: PlatformDb):
    from app.modules.events.service import to_out

    _, actor = await arena_context(db, current)
    rows = list(
        (
            await db.scalars(
                select(Event)
                .where(Event.tenant_id == actor.tenant.id, Event.deleted_at.is_(None))
                .order_by(Event.starts_at.desc())
            )
        ).all()
    )
    return await to_out(db, actor.tenant.id, rows)


@router.post("/events", status_code=201)
async def owner_create_event(body: EventCreate, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.events import service

    _, actor = await arena_context(db, current)
    row = await service.create_event(db, actor, body, meta)
    return {"id": row.id, "name": row.name}


class EventAction(Schema):
    status: EventStatus


@router.post("/events/{event_id}/status")
async def owner_event_status(event_id: uuid.UUID, body: EventAction, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.events import service

    config, actor = await arena_context(db, current)
    row = await service.transition(db, actor, event_id, body.status, meta)
    await sync_arena(db, config, current, meta)
    return {"id": row.id, "status": row.status}


@router.get("/events/{event_id}/fights")
async def owner_fights(event_id: uuid.UUID, current: PlatformSuperAdmin, db: PlatformDb):
    from app.modules.matches import service

    _, actor = await arena_context(db, current)
    rows = await service.list_for_event(db, actor.tenant.id, event_id)
    return await service.to_out(db, actor.tenant.id, rows)


@router.post("/events/{event_id}/fights", status_code=201)
async def owner_create_fight(event_id: uuid.UUID, body: MatchCreate, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.matches import service

    config, actor = await arena_context(db, current)
    row = await service.create_match(db, actor, event_id, body, meta)
    await sync_arena(db, config, current, meta)
    return {"id": row.id, "match_number": row.match_number}


class FightAction(Schema):
    action: Literal["start", "open", "close", "cancel"]
    reason: str = Field(default="Cancelled by OWNER", min_length=3, max_length=300)


@router.post("/fights/{match_id}/action")
async def owner_fight_action(match_id: uuid.UUID, body: FightAction, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.betting import service as betting
    from app.modules.matches import service as matches

    config, actor = await arena_context(db, current)
    if body.action == "start":
        await matches.start_match(db, actor, match_id, meta)
    elif body.action == "open":
        await betting.open_betting(db, actor, match_id, meta, by="owner_global")
    elif body.action == "close":
        await betting.close_betting(db, actor, match_id, meta, by="owner_global")
    else:
        await matches.cancel_match(db, actor, match_id, body.reason, meta)
    await sync_arena(db, config, current, meta)
    return {"message": "Official fight updated"}


@router.post("/fights/{match_id}/result")
async def owner_result(match_id: uuid.UUID, body: ResultDeclare, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.results import service

    config, actor = await arena_context(db, current)
    await service.declare(db, actor, match_id, body.outcome, body.notes, meta, _workflow=True)
    await sync_arena(db, config, current, meta)
    return {"message": "Official result settled in each tenant"}


@router.put("/fights/{match_id}/result")
async def owner_correct_result(match_id: uuid.UUID, body: ResultCorrect, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    from app.modules.results import service

    config, actor = await arena_context(db, current)
    await service.correct(db, actor, match_id, body.outcome, body.reason, body.notes, meta, _workflow=True)
    await sync_arena(db, config, current, meta)
    return {"message": "Official correction settled in each tenant"}
