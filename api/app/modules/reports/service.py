import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.permissions import P
from app.modules.agents.service import downline_ids_select
from app.modules.audit.models import AuditLog
from app.modules.demo_points.models import PointAccount, PointLedgerEntry, PointTransaction
from app.modules.events.models import Event
from app.modules.matches.models import Match, MatchStatus
from app.modules.results.models import MatchOutcome, MatchResult
from app.modules.tenants.models import TenantSettings
from app.modules.users.models import TenantUser


async def tenant_tz(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    tz = (await db.execute(select(TenantSettings.timezone).where(TenantSettings.tenant_id == tenant_id))).scalar_one_or_none()
    return tz or "UTC"


def window(tz: str, start: date | None, end: date | None, default_days: int = 30) -> tuple[date, date, datetime, datetime]:
    zone = ZoneInfo(tz)
    today = datetime.now(zone).date()
    end = end or today
    start = start or end - timedelta(days=default_days - 1)
    if start > end:
        start, end = end, start
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    lo = datetime.combine(start, time.min, zone)
    hi = datetime.combine(end + timedelta(days=1), time.min, zone)
    return start, end, lo, hi


def _local_day(col, tz: str):  # type: ignore[no-untyped-def]
    return cast(func.timezone(tz, col), Date)


def scoped_user_ids(actor: CurrentUser):  # type: ignore[no-untyped-def]
    """None when the actor sees the whole tenant, else a subquery of visible user ids."""
    if actor.has_any(P.USERS_VIEW_ALL, P.USERS_MANAGE):
        return None
    tenant_id = actor.tenant.id
    return select(TenantUser.id).where(
        TenantUser.tenant_id == tenant_id,
        or_(TenantUser.id == actor.id, TenantUser.id.in_(downline_ids_select(tenant_id, actor.id))),
    )


async def activity(db: AsyncSession, actor: CurrentUser, start: date | None, end: date | None) -> dict:
    tenant_id = actor.tenant.id
    tz = await tenant_tz(db, tenant_id)
    d0, d1, lo, hi = window(tz, start, end)
    scope = scoped_user_ids(actor)

    login_q = (
        select(_local_day(AuditLog.created_at, tz).label("d"), func.count())
        .where(
            AuditLog.tenant_id == tenant_id, AuditLog.action == "auth.login", AuditLog.created_at >= lo, AuditLog.created_at < hi
        )
        .group_by("d")
    )
    users_q = (
        select(_local_day(TenantUser.created_at, tz).label("d"), func.count())
        .where(TenantUser.tenant_id == tenant_id, TenantUser.created_at >= lo, TenantUser.created_at < hi)
        .group_by("d")
    )
    tx_q = (
        select(
            _local_day(PointTransaction.created_at, tz).label("d"),
            func.count(),
            func.coalesce(func.sum(PointTransaction.amount), 0),
        )
        .where(PointTransaction.tenant_id == tenant_id, PointTransaction.created_at >= lo, PointTransaction.created_at < hi)
        .group_by("d")
    )
    matches_q = (
        select(_local_day(MatchResult.declared_at, tz).label("d"), func.count())
        .where(MatchResult.tenant_id == tenant_id, MatchResult.declared_at >= lo, MatchResult.declared_at < hi)
        .group_by("d")
    )
    if scope is not None:
        login_q = login_q.where(AuditLog.actor_id.in_(scope))
        users_q = users_q.where(TenantUser.id.in_(scope))
        accounts = select(PointAccount.id).where(PointAccount.tenant_id == tenant_id, PointAccount.user_id.in_(scope))
        tx_q = tx_q.where(or_(PointTransaction.from_account_id.in_(accounts), PointTransaction.to_account_id.in_(accounts)))

    logins = dict((await db.execute(login_q)).tuples().all())
    new_users = dict((await db.execute(users_q)).tuples().all())
    txs = {d: (int(n), int(v)) for d, n, v in (await db.execute(tx_q)).tuples().all()}
    completed = dict((await db.execute(matches_q)).tuples().all())

    rows = []
    day = d0
    while day <= d1:
        n_tx, volume = txs.get(day, (0, 0))
        rows.append(
            {
                "date": day.isoformat(),
                "logins": int(logins.get(day, 0)),
                "new_accounts": int(new_users.get(day, 0)),
                "point_transactions": n_tx,
                "points_moved": volume,
                "results_declared": int(completed.get(day, 0)),
            }
        )
        day += timedelta(days=1)
    totals = {
        k: sum(r[k] for r in rows) for k in ("logins", "new_accounts", "point_transactions", "points_moved", "results_declared")
    }
    return {
        "timezone": tz,
        "from": d0.isoformat(),
        "to": d1.isoformat(),
        "scope": "tenant" if scope is None else "downline",
        "rows": rows,
        "totals": totals,
    }


async def points_report(db: AsyncSession, actor: CurrentUser, start: date | None, end: date | None) -> dict:
    tenant_id = actor.tenant.id
    tz = await tenant_tz(db, tenant_id)
    d0, d1, lo, hi = window(tz, start, end)
    scope = scoped_user_ids(actor)
    received = func.coalesce(func.sum(case((PointLedgerEntry.delta > 0, PointLedgerEntry.delta), else_=0)), 0)
    sent = func.coalesce(func.sum(case((PointLedgerEntry.delta < 0, -PointLedgerEntry.delta), else_=0)), 0)
    movement = (
        select(PointLedgerEntry.account_id, received.label("received"), sent.label("sent"))
        .where(PointLedgerEntry.tenant_id == tenant_id, PointLedgerEntry.created_at >= lo, PointLedgerEntry.created_at < hi)
        .group_by(PointLedgerEntry.account_id)
        .subquery()
    )
    stmt = (
        select(
            TenantUser.id,
            TenantUser.username,
            TenantUser.full_name,
            TenantUser.account_type,
            PointAccount.balance,
            func.coalesce(movement.c.received, 0),
            func.coalesce(movement.c.sent, 0),
        )
        .join(PointAccount, PointAccount.user_id == TenantUser.id)
        .outerjoin(movement, movement.c.account_id == PointAccount.id)
        .where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
        .order_by(PointAccount.balance.desc(), TenantUser.username)
        .limit(5000)
    )
    if scope is not None:
        stmt = stmt.where(TenantUser.id.in_(scope))
    rows = [
        {
            "user_id": str(uid),
            "username": u,
            "full_name": n,
            "account_type": t.value,
            "balance": int(b),
            "received": int(r),
            "sent": int(s),
        }
        for uid, u, n, t, b, r, s in (await db.execute(stmt)).tuples().all()
    ]
    return {
        "timezone": tz,
        "from": d0.isoformat(),
        "to": d1.isoformat(),
        "scope": "tenant" if scope is None else "downline",
        "rows": rows,
        "totals": {
            "balance": sum(r["balance"] for r in rows),
            "received": sum(r["received"] for r in rows),
            "sent": sum(r["sent"] for r in rows),
        },
    }


async def matches_report(db: AsyncSession, actor: CurrentUser, start: date | None, end: date | None) -> dict:
    tenant_id = actor.tenant.id
    tz = await tenant_tz(db, tenant_id)
    d0, d1, lo, hi = window(tz, start, end)
    outcome_cols = [func.sum(case((MatchResult.outcome == o, 1), else_=0)).label(o.value) for o in MatchOutcome]
    stmt = (
        select(
            Event.id,
            Event.name,
            Event.starts_at,
            Event.status,
            func.count(Match.id),
            func.sum(case((Match.status == MatchStatus.COMPLETED, 1), else_=0)),
            func.sum(case((Match.status == MatchStatus.CANCELLED, 1), else_=0)),
            *outcome_cols,
        )
        .outerjoin(Match, (Match.event_id == Event.id) & Match.deleted_at.is_(None))
        .outerjoin(MatchResult, MatchResult.match_id == Match.id)
        .where(Event.tenant_id == tenant_id, Event.deleted_at.is_(None), Event.starts_at >= lo, Event.starts_at < hi)
        .group_by(Event.id)
        .order_by(Event.starts_at.desc())
    )
    rows = []
    for eid, name, starts, status, total, done, cancelled, *outcomes in (await db.execute(stmt)).tuples().all():
        row = {
            "event_id": str(eid),
            "event": name,
            "starts_at": starts.isoformat(),
            "status": status.value,
            "matches": int(total or 0),
            "completed": int(done or 0),
            "cancelled": int(cancelled or 0),
        }
        for o, n in zip(MatchOutcome, outcomes, strict=True):
            row[o.value] = int(n or 0)
        rows.append(row)
    totals = {k: sum(r[k] for r in rows) for k in ["matches", "completed", "cancelled", *[o.value for o in MatchOutcome]]}
    return {"timezone": tz, "from": d0.isoformat(), "to": d1.isoformat(), "rows": rows, "totals": totals}


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        # Neutralise spreadsheet formula injection in user-controlled text.
        writer.writerow({k: (f"'{v}" if isinstance(v, str) and v[:1] in "=+-@\t\r" else v) for k, v in row.items()})
    return buf.getvalue()
