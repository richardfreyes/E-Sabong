"""Read-only staff views of simulated betting: fight summaries (Declarator monitor, admin overview),
play wallets, the wallet ledger and the reconciliation report. Nothing here writes."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import Select, case, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.balances.models import BalanceOperation
from app.modules.betting import odds
from app.modules.betting.models import (
    Bet,
    BetSide,
    BetStatus,
    BookState,
    FightBook,
    PlayWallet,
    PlayWalletEntry,
    WalletEntryKind,
)
from app.modules.betting.schemas import (
    FightBookSummary,
    Obligations,
    PlayerStake,
    Reconciliation,
    ReconciliationChecks,
    SettlementSummary,
    StatementSummary,
    WalletEntryRow,
    WalletRow,
)
from app.modules.betting.service import _snapshot
from app.modules.events.models import Event, EventStatus
from app.modules.matches.models import Match
from app.modules.users.models import TenantUser
from app.shared.schemas import Page, PageParams

ZERO = odds.ZERO


def _dec(value: object) -> Decimal:
    return Decimal(value or 0).quantize(odds.CENT)


async def _player_stakes(
    db: AsyncSession, tenant_id: uuid.UUID, match_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PlayerStake]]:
    """Every player's Meron, Wala and Draw stake on these fights, one row per player."""
    rows = (
        await db.execute(
            select(Bet.match_id, TenantUser.username, TenantUser.full_name, Bet.side, func.coalesce(func.sum(Bet.amount), 0))
            .join(TenantUser, (TenantUser.id == Bet.user_id) & (TenantUser.tenant_id == tenant_id))
            .where(Bet.tenant_id == tenant_id, Bet.match_id.in_(match_ids))
            .group_by(Bet.match_id, TenantUser.username, TenantUser.full_name, Bet.side)
            .order_by(func.lower(TenantUser.username), Bet.side)
        )
    ).all()
    built: dict[uuid.UUID, dict[str, dict[str, object]]] = {}
    for match_id, username, full_name, side, amount in rows:
        by_player = built.setdefault(match_id, {})
        row = by_player.setdefault(
            username, {"username": username, "full_name": full_name, "meron": ZERO, "wala": ZERO, "draw": ZERO}
        )
        row[side.value] = _dec(amount)
    return {match_id: [PlayerStake.model_validate(row) for row in by_player.values()] for match_id, by_player in built.items()}


async def fight_summaries(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    match_ids: list[uuid.UUID] | None = None,
    event_id: uuid.UUID | None = None,
    states: list[BookState] | None = None,
    live_events_only: bool = False,
    params: PageParams | None = None,
) -> tuple[list[FightBookSummary], int]:
    stmt = (
        select(FightBook, Match, Event.name, Event.id)
        .join(Match, (Match.id == FightBook.match_id) & (Match.tenant_id == tenant_id))
        .join(Event, (Event.id == Match.event_id) & (Event.tenant_id == tenant_id))
        .where(FightBook.tenant_id == tenant_id, Match.deleted_at.is_(None), Event.deleted_at.is_(None))
    )
    if match_ids is not None:
        stmt = stmt.where(FightBook.match_id.in_(match_ids))
    if event_id is not None:
        stmt = stmt.where(Match.event_id == event_id)
    if states:
        stmt = stmt.where(FightBook.state.in_(states))
    if live_events_only:
        stmt = stmt.where(Event.status == EventStatus.LIVE)
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    # Open books first, then the most recently changed.
    order = (
        case((FightBook.state == BookState.OPEN, 0), (FightBook.state == BookState.CLOSED, 1), else_=2),
        FightBook.updated_at.desc(),
    )
    stmt = stmt.order_by(*order)
    if params is not None:
        stmt = stmt.offset(params.offset).limit(params.page_size)
    rows = (await db.execute(stmt)).all()
    if not rows:
        return [], int(total)

    ids = [book.match_id for book, *_ in rows]
    players = await _player_stakes(db, tenant_id, ids)
    locked_pays = func.round(Bet.amount * Bet.multiplier, 2)
    totals = {
        r.match_id: r
        for r in (
            await db.execute(
                select(
                    Bet.match_id,
                    func.coalesce(func.sum(case((Bet.side == BetSide.DRAW, locked_pays), else_=0)), 0).label("draw_pays"),
                    func.coalesce(func.sum(case((Bet.status == BetStatus.WON, Bet.payout), else_=0)), 0).label("won"),
                    func.coalesce(func.sum(case((Bet.status == BetStatus.REFUNDED, Bet.payout), else_=0)), 0).label("refunded"),
                    func.coalesce(func.sum(case((Bet.payout > Bet.credited, Bet.payout - Bet.credited), else_=0)), 0).label(
                        "uncollected"
                    ),
                )
                .where(Bet.tenant_id == tenant_id, Bet.match_id.in_(ids))
                .group_by(Bet.match_id)
            )
        ).all()
    }
    out = []
    for book, match, event_name, ev_id in rows:
        agg = totals.get(book.match_id)
        stakes = book.meron_pool + book.dummy_meron + book.wala_pool + book.dummy_wala + book.draw_pool
        snapshot = await _snapshot(db, tenant_id, book.match_id, book)
        settled = book.state == BookState.SETTLED
        owed = odds.desk_obligations(
            book.meron_pool,
            book.wala_pool,
            book.dummy_meron,
            book.dummy_wala,
            book.draw_pool,
            _dec(agg.draw_pays if agg else 0),
            book.commission_rate,
            odds.estimated_pays(book.meron_pool, snapshot.odds.meron_display) or odds.ZERO,
            odds.estimated_pays(book.wala_pool, snapshot.odds.wala_display) or odds.ZERO,
        )
        out.append(
            FightBookSummary(
                **snapshot.model_dump(),
                event_id=ev_id,
                event_name=event_name,
                match_number=match.match_number,
                match_status=match.status.value,
                stakes_total=stakes,
                players=players.get(book.match_id, []),
                dummy_meron=book.dummy_meron,
                dummy_wala=book.dummy_wala,
                obligations=None if settled else Obligations(**owed),
                settlement=SettlementSummary(
                    outcome=book.outcome or "",
                    revision=book.result_revision,
                    stakes=stakes,
                    paid_to_winners=_dec(agg.won if agg else 0),
                    refunds=_dec(agg.refunded if agg else 0),
                    commission=book.commission_amount or odds.ZERO,
                    uncollected=_dec(agg.uncollected if agg else 0),
                    settled_at=book.settled_at,
                )
                if settled
                else None,
                opened_at=book.opened_at,
                closed_at=book.closed_at,
            )
        )
    return out, int(total)


async def wallets(db: AsyncSession, tenant_id: uuid.UUID, params: PageParams) -> Page[WalletRow]:
    by_kind = (
        select(
            PlayWalletEntry.wallet_id,
            func.sum(
                case(
                    (
                        PlayWalletEntry.kind.in_(
                            [WalletEntryKind.GRANT, WalletEntryKind.ACCOUNT_GRANT, WalletEntryKind.UNIFORM_GRANT]
                        ),
                        PlayWalletEntry.delta,
                    ),
                    else_=0,
                )
            ).label("granted"),
            func.sum(
                case(
                    (PlayWalletEntry.kind.in_([WalletEntryKind.RESET, WalletEntryKind.CORRECTION]), PlayWalletEntry.delta),
                    else_=0,
                )
            ).label("adjusted"),
            func.sum(case((PlayWalletEntry.kind == WalletEntryKind.STAKE, -PlayWalletEntry.delta), else_=0)).label("staked"),
            func.sum(
                case(
                    (
                        PlayWalletEntry.kind.in_([WalletEntryKind.PAYOUT, WalletEntryKind.REFUND, WalletEntryKind.RESETTLEMENT]),
                        PlayWalletEntry.delta,
                    ),
                    else_=0,
                )
            ).label("returned"),
            func.count().label("entries"),
        )
        .where(PlayWalletEntry.tenant_id == tenant_id)
        .group_by(PlayWalletEntry.wallet_id)
        .subquery()
    )
    from app.modules.simulations.models import SimulationMatch, SimulationStake, SimulationState

    combined = union_all(
        select(Bet.user_id.label("user_id"), Bet.amount.label("amount")).where(
            Bet.tenant_id == tenant_id,
            Bet.status == BetStatus.ACCEPTED,
        ),
        select(SimulationStake.player_id.label("user_id"), SimulationStake.amount.label("amount"))
        .join(
            SimulationMatch,
            (SimulationMatch.id == SimulationStake.match_id) & (SimulationMatch.tenant_id == SimulationStake.tenant_id),
        )
        .where(SimulationStake.tenant_id == tenant_id, SimulationMatch.state != SimulationState.SETTLED),
    ).subquery()
    open_stakes = select(combined.c.user_id, func.sum(combined.c.amount).label("open")).group_by(combined.c.user_id).subquery()
    stmt = (
        select(PlayWallet, TenantUser.username, TenantUser.full_name, by_kind, open_stakes.c.open)
        .join(TenantUser, (TenantUser.id == PlayWallet.user_id) & (TenantUser.tenant_id == tenant_id))
        .outerjoin(by_kind, by_kind.c.wallet_id == PlayWallet.id)
        .outerjoin(open_stakes, open_stakes.c.user_id == PlayWallet.user_id)
        .where(PlayWallet.tenant_id == tenant_id)
    )
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(TenantUser.username.ilike(like), TenantUser.full_name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(PlayWallet.balance.desc(), TenantUser.username).offset(params.offset).limit(params.page_size)
        )
    ).all()
    items = [
        WalletRow(
            user_id=r[0].user_id,
            username=r.username,
            full_name=r.full_name,
            balance=r[0].balance,
            granted=_dec(r.granted),
            adjusted=_dec(r.adjusted),
            staked=_dec(r.staked),
            returned=_dec(r.returned),
            open_stakes=_dec(r.open),
            entries=int(r.entries or 0),
            updated_at=r[0].updated_at,
        )
        for r in rows
    ]
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)


async def entries(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    params: PageParams,
    *,
    kind: WalletEntryKind | None = None,
    user_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    user_scope: Select | None = None,  # type: ignore[type-arg]
    entry_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
) -> Page[WalletEntryRow]:
    """Ledger rows, newest first. ``user_scope`` (a select of user ids) limits the accounts shown."""
    stmt = (
        select(PlayWalletEntry, PlayWallet.user_id, TenantUser.username, Match.match_number, Event.name, Bet, BalanceOperation)
        .join(PlayWallet, (PlayWallet.id == PlayWalletEntry.wallet_id) & (PlayWallet.tenant_id == tenant_id))
        .join(TenantUser, (TenantUser.id == PlayWallet.user_id) & (TenantUser.tenant_id == tenant_id))
        .outerjoin(Match, (Match.id == PlayWalletEntry.match_id) & (Match.tenant_id == tenant_id))
        .outerjoin(Event, (Event.id == Match.event_id) & (Event.tenant_id == tenant_id))
        .outerjoin(Bet, (Bet.id == PlayWalletEntry.bet_id) & (Bet.tenant_id == tenant_id))
        .outerjoin(
            BalanceOperation,
            (BalanceOperation.id == PlayWalletEntry.operation_id) & (BalanceOperation.tenant_id == tenant_id),
        )
        .where(PlayWalletEntry.tenant_id == tenant_id)
    )
    stmt = _in_range(stmt, PlayWalletEntry.created_at, date_from, date_to)
    if user_scope is not None:
        stmt = stmt.where(PlayWallet.user_id.in_(user_scope))
    if entry_id:
        stmt = stmt.where(PlayWalletEntry.id == entry_id)
    if operation_id:
        stmt = stmt.where(PlayWalletEntry.operation_id == operation_id)
    if kind:
        stmt = stmt.where(PlayWalletEntry.kind == kind)
    if user_id:
        stmt = stmt.where(PlayWallet.user_id == user_id)
    if match_id:
        stmt = stmt.where(PlayWalletEntry.match_id == match_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(
            or_(
                TenantUser.username.ilike(like),
                TenantUser.full_name.ilike(like),
                PlayWalletEntry.idempotency_key.ilike(like),
                BalanceOperation.reference.ilike(like),
                PlayWalletEntry.memo.ilike(like),
                Event.name.ilike(like),
            )
        )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(PlayWalletEntry.created_at.desc(), PlayWalletEntry.id.desc())
            .offset(params.offset)
            .limit(params.page_size)
        )
    ).all()
    items = [
        WalletEntryRow(
            id=e.id,
            created_at=e.created_at,
            user_id=uid,
            username=username,
            kind=e.kind.value,
            delta=e.delta,
            balance_after=e.balance_after,
            reference=e.idempotency_key,
            memo=e.memo,
            match_id=e.match_id,
            match_number=number,
            event_name=event_name,
            bet_id=bet.id if bet else None,
            bet_side=bet.side.value if bet else None,
            bet_status=bet.status.value if bet else None,
            bet_amount=bet.amount if bet else None,
            bet_credited=bet.credited if bet else None,
            balance_before=e.balance_after - e.delta,
            operation_id=operation.id if operation else None,
            operation_reference=operation.reference if operation else None,
            operation_kind=operation.kind.value if operation else None,
            operation_status=operation.status.value if operation else None,
            operation_remarks=operation.remarks if operation else None,
        )
        for e, uid, username, number, event_name, bet, operation in rows
    ]
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)


def _in_range(stmt, column, date_from: date | None, date_to: date | None):  # type: ignore[no-untyped-def]
    """Inclusive UTC calendar-day range on a timestamp column."""
    if date_from:
        stmt = stmt.where(column >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to:
        stmt = stmt.where(column < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC))
    return stmt


async def statement_summary(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StatementSummary:
    """Read-only statement totals for one player; the balance is always the current one."""
    user = (
        await db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("Account not found")
    wallet = (
        await db.execute(select(PlayWallet).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id))
    ).scalar_one_or_none()
    zero = Decimal("0.00")
    sums: dict[WalletEntryKind, Decimal] = {}
    entries = 0
    if wallet is not None:
        ledger = _in_range(
            select(PlayWalletEntry.kind, func.sum(PlayWalletEntry.delta), func.count())
            .where(PlayWalletEntry.tenant_id == tenant_id, PlayWalletEntry.wallet_id == wallet.id)
            .group_by(PlayWalletEntry.kind),
            PlayWalletEntry.created_at,
            date_from,
            date_to,
        )
        for kind, total, n in (await db.execute(ledger)).all():
            sums[kind] = total or zero
            entries += int(n)
    bets = _in_range(
        select(Bet.status, func.count(), func.coalesce(func.sum(Bet.amount), 0))
        .where(Bet.tenant_id == tenant_id, Bet.user_id == user_id)
        .group_by(Bet.status),
        Bet.created_at,
        date_from,
        date_to,
    )
    by_status = {status: (int(n), Decimal(amount)) for status, n, amount in (await db.execute(bets)).all()}
    count = lambda s: by_status.get(s, (0, zero))[0]  # noqa: E731
    amount = lambda s: by_status.get(s, (0, zero))[1]  # noqa: E731
    staked = -sums.get(WalletEntryKind.STAKE, zero)
    winnings = sums.get(WalletEntryKind.PAYOUT, zero)
    refunds = sums.get(WalletEntryKind.REFUND, zero)
    corrections = sums.get(WalletEntryKind.RESETTLEMENT, zero)
    account = sums.get(WalletEntryKind.ACCOUNT_GRANT, zero)
    uniform = sums.get(WalletEntryKind.UNIFORM_GRANT, zero)
    resets = sums.get(WalletEntryKind.RESET, zero)
    adjustments = sums.get(WalletEntryKind.CORRECTION, zero)
    transfers = sums.get(WalletEntryKind.TRANSFER, zero)
    return StatementSummary(
        user_id=user.id,
        username=user.username,
        balance=wallet.balance if wallet else zero,
        granted=sums.get(WalletEntryKind.GRANT, zero),
        staked=staked,
        winnings_credited=winnings,
        refunds=refunds,
        corrections=corrections,
        net_result=winnings + refunds + corrections - staked,
        account_grants=account,
        uniform_grants=uniform,
        resets=resets,
        balance_corrections=adjustments,
        transfers=transfers,
        bets=sum(n for n, _ in by_status.values()),
        bets_won=count(BetStatus.WON),
        bets_lost=count(BetStatus.LOST),
        bets_refunded=count(BetStatus.REFUNDED),
        bets_open=count(BetStatus.ACCEPTED),
        lost_stakes=amount(BetStatus.LOST),
        open_stakes=amount(BetStatus.ACCEPTED),
        entries=entries,
    )


async def reconciliation(db: AsyncSession, tenant_id: uuid.UUID) -> Reconciliation:
    kinds = dict(
        (
            await db.execute(
                select(PlayWalletEntry.kind, func.sum(PlayWalletEntry.delta))
                .where(PlayWalletEntry.tenant_id == tenant_id)
                .group_by(PlayWalletEntry.kind)
            )
        )
        .tuples()
        .all()
    )
    grants = (
        await db.execute(
            select(func.count()).where(PlayWalletEntry.tenant_id == tenant_id, PlayWalletEntry.kind == WalletEntryKind.GRANT)
        )
    ).scalar_one()
    in_wallets, wallet_count = (
        await db.execute(
            select(func.coalesce(func.sum(PlayWallet.balance), 0), func.count()).where(PlayWallet.tenant_id == tenant_id)
        )
    ).one()
    ledger_by_wallet = (
        select(PlayWalletEntry.wallet_id, func.sum(PlayWalletEntry.delta).label("total"))
        .where(PlayWalletEntry.tenant_id == tenant_id)
        .group_by(PlayWalletEntry.wallet_id)
        .subquery()
    )
    wallet_mismatches = (
        await db.execute(
            select(func.count())
            .select_from(PlayWallet)
            .outerjoin(ledger_by_wallet, ledger_by_wallet.c.wallet_id == PlayWallet.id)
            .where(PlayWallet.tenant_id == tenant_id, PlayWallet.balance != func.coalesce(ledger_by_wallet.c.total, 0))
        )
    ).scalar_one()
    bet_pools = (
        select(
            Bet.match_id,
            func.sum(case((Bet.side == BetSide.MERON, Bet.amount), else_=0)).label("meron"),
            func.sum(case((Bet.side == BetSide.WALA, Bet.amount), else_=0)).label("wala"),
            func.sum(case((Bet.side == BetSide.DRAW, Bet.amount), else_=0)).label("draw"),
        )
        .where(Bet.tenant_id == tenant_id)
        .group_by(Bet.match_id)
        .subquery()
    )
    pool_mismatches = (
        await db.execute(
            select(func.count())
            .select_from(FightBook)
            .outerjoin(bet_pools, bet_pools.c.match_id == FightBook.match_id)
            .where(
                FightBook.tenant_id == tenant_id,
                or_(
                    FightBook.meron_pool != func.coalesce(bet_pools.c.meron, 0),
                    FightBook.wala_pool != func.coalesce(bet_pools.c.wala, 0),
                    FightBook.draw_pool != func.coalesce(bet_pools.c.draw, 0),
                ),
            )
        )
    ).scalar_one()
    at_stake, uncollected_bets, uncollected_amount = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((Bet.status == BetStatus.ACCEPTED, Bet.amount), else_=0)), 0),
                func.count().filter(Bet.payout > Bet.credited),
                func.coalesce(func.sum(case((Bet.payout > Bet.credited, Bet.payout - Bet.credited), else_=0)), 0),
            ).where(Bet.tenant_id == tenant_id)
        )
    ).one()
    commission = (
        await db.execute(
            select(func.coalesce(func.sum(FightBook.commission_amount), 0)).where(
                FightBook.tenant_id == tenant_id, FightBook.state == BookState.SETTLED
            )
        )
    ).scalar_one()

    # Both game engines use the SAME wallet ledger; active neutral stakes remain
    # committed to their pools until a settlement receipt is committed.
    from app.modules.simulations.models import SimulationMatch, SimulationSettlement, SimulationStake, SimulationState

    simulation_active = await db.scalar(
        select(func.coalesce(func.sum(SimulationStake.amount), 0))
        .join(
            SimulationMatch,
            (SimulationMatch.id == SimulationStake.match_id) & (SimulationMatch.tenant_id == SimulationStake.tenant_id),
        )
        .where(SimulationStake.tenant_id == tenant_id, SimulationMatch.state != SimulationState.SETTLED)
    )
    simulation_fee = await db.scalar(
        select(func.coalesce(func.sum(SimulationSettlement.fee_amount), 0)).where(
            SimulationSettlement.tenant_id == tenant_id,
        )
    )
    simulation_pools = (
        select(
            SimulationStake.match_id,
            func.sum(case((SimulationStake.side == "team_a", SimulationStake.amount), else_=0)).label("a"),
            func.sum(case((SimulationStake.side == "team_b", SimulationStake.amount), else_=0)).label("b"),
        )
        .where(SimulationStake.tenant_id == tenant_id)
        .group_by(SimulationStake.match_id)
        .subquery()
    )
    simulation_mismatches = await db.scalar(
        select(func.count())
        .select_from(SimulationMatch)
        .outerjoin(
            simulation_pools,
            simulation_pools.c.match_id == SimulationMatch.id,
        )
        .where(
            SimulationMatch.tenant_id == tenant_id,
            or_(
                SimulationMatch.team_a_pool != func.coalesce(simulation_pools.c.a, 0),
                SimulationMatch.team_b_pool != func.coalesce(simulation_pools.c.b, 0),
            ),
        )
    )
    at_stake = _dec(at_stake) + _dec(simulation_active)
    commission = _dec(commission) + _dec(simulation_fee)
    pool_mismatches += int(simulation_mismatches or 0)

    granted = _dec(kinds.get(WalletEntryKind.GRANT))
    staked = -_dec(kinds.get(WalletEntryKind.STAKE))
    paid = _dec(kinds.get(WalletEntryKind.PAYOUT))
    refunded = _dec(kinds.get(WalletEntryKind.REFUND))
    resettled = _dec(kinds.get(WalletEntryKind.RESETTLEMENT))
    account = _dec(kinds.get(WalletEntryKind.ACCOUNT_GRANT))
    uniform = _dec(kinds.get(WalletEntryKind.UNIFORM_GRANT))
    reset_net = _dec(kinds.get(WalletEntryKind.RESET))
    corrected = _dec(kinds.get(WalletEntryKind.CORRECTION))
    # Transfers move points between wallets, so their tenant-wide net is zero when both sides posted.
    transferred = _dec(kinds.get(WalletEntryKind.TRANSFER))
    administered = account + uniform + reset_net + corrected
    ledger_total = granted + administered + transferred - staked + paid + refunded + resettled
    at_stake = _dec(at_stake)
    house = staked - paid - refunded - resettled - at_stake
    checks = ReconciliationChecks(
        wallets_match_ledger=_dec(in_wallets) == ledger_total,
        wallet_mismatches=int(wallet_mismatches),
        pool_mismatches=int(pool_mismatches),
        uncollected_bets=int(uncollected_bets),
        uncollected_amount=_dec(uncollected_amount),
    )
    return Reconciliation(
        granted=granted,
        grants=int(grants),
        staked=staked,
        paid_to_winners=paid,
        refunded=refunded,
        resettled=resettled,
        account_granted=account,
        uniform_granted=uniform,
        reset_net=reset_net,
        corrections_net=corrected,
        in_wallets=_dec(in_wallets),
        wallets=int(wallet_count),
        at_stake=at_stake,
        house_result=house,
        commission=_dec(commission),
        checks=checks,
        balanced=checks.wallets_match_ledger and not checks.wallet_mismatches and not checks.pool_mismatches,
    )
