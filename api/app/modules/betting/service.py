"""Simulated fight betting with play balances.

Lifecycle of a fight's book: an operator opens betting on a fight of a live event; players
stake their play balance on MERON, WALA or DRAW while the book is open; betting closes when an
operator closes it, a Declarator submits a result, or a result is recorded, and the MERON/WALA
multipliers are locked at that moment; the official result then settles every bet (see
``odds.settle`` for the rules). Cancelled fights and events refund every stake.

Concurrency (READ COMMITTED):

* Placing a bet takes a shared lock on the event and the fight, as predictions do, so it runs
  alongside other bets but never alongside a close, a result or a cancellation, which take
  those rows ``FOR UPDATE``. A bet accepted before a close is always in the frozen pool; one
  arriving after it sees the closed book.
* The fight's book row is locked for the duration of a bet, so pool totals and ``version``
  move in a single order that clients can rely on to drop stale frames.
* A bet then locks the player's wallet; two bets by one player serialise there, which is where
  the balance, the per-fight limit and the request key are re-checked.
* Settlement locks the book, then every affected wallet in ascending id order.

Idempotency: a bet's ``request_id`` is unique per player, so a retried request replays the
original bet. Every wallet posting carries a unique key (``stake:<bet>``, ``settle:<bet>:<n>``),
and settlement always moves each bet from what has already been credited to what it is now
owed, so repeating it changes nothing and a corrected result moves only the difference.
"""

import uuid
from decimal import ROUND_DOWN, Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.betting import odds
from app.modules.betting.models import (
    Bet,
    BetPricing,
    BetSide,
    BetStatus,
    BettingSettings,
    BookState,
    FightBook,
    PlayWallet,
    PlayWalletEntry,
    WalletEntryKind,
)
from app.modules.betting.schemas import (
    AdminBookOut,
    BetHistoryItem,
    BetIn,
    BetOut,
    BetPlaced,
    BettingConfigOut,
    BettingSettingsUpdate,
    BookOut,
    DummyStakeIn,
    FightPools,
    Limits,
    MyPosition,
    Odds,
    OddsUpdate,
    PlayerBookOut,
    Pools,
    SidePosition,
    WalletOut,
)
from app.modules.commissions import service as commissions
from app.modules.events.models import Event, EventStatus
from app.modules.matches.models import Match, MatchStatus
from app.modules.predictions.service import window_open_clause
from app.modules.realtime.publisher import notify_staff, publish_after_commit
from app.modules.results.models import MatchResult
from app.modules.users.models import AccountType
from app.shared.schemas import Page, PageParams

log = structlog.get_logger("betting")

ZERO = odds.ZERO
POOL_COLUMNS = {BetSide.MERON: "meron_pool", BetSide.WALA: "wala_pool", BetSide.DRAW: "draw_pool"}
VISIBLE_EVENT_STATUSES = (EventStatus.SCHEDULED, EventStatus.LIVE, EventStatus.COMPLETED)


# ---- settings -------------------------------------------------------------------------------


async def settings_for(db: AsyncSession, tenant_id: uuid.UUID) -> BettingSettings:
    row = await db.get(BettingSettings, tenant_id)
    if row is None:
        await db.execute(insert(BettingSettings).values(tenant_id=tenant_id).on_conflict_do_nothing())
        row = await db.get(BettingSettings, tenant_id)
        assert row is not None
    return row


async def update_settings(
    db: AsyncSession, actor: CurrentUser, data: BettingSettingsUpdate, meta: RequestMeta
) -> BettingSettings:
    row = await settings_for(db, actor.tenant.id)
    row = (
        await db.execute(
            select(BettingSettings)
            .where(BettingSettings.tenant_id == actor.tenant.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    fields = data.model_dump(exclude_unset=True, exclude_none=True)
    before = {k: getattr(row, k) for k in fields}
    merged = {**{k: getattr(row, k) for k in ("min_bet", "max_bet", "max_fight_stake")}, **fields}
    if merged["max_bet"] < merged["min_bet"]:
        raise Unprocessable("The maximum bet must be at least the minimum bet", code="invalid_limits")
    if merged["max_fight_stake"] < merged["max_bet"]:
        raise Unprocessable("The per-fight limit must be at least the maximum bet", code="invalid_limits")
    for k, v in fields.items():
        setattr(row, k, v)
    await db.flush()
    # Decimals are recorded as strings: audit changes are stored as JSON.
    changes = audit.diff({k: str(v) for k, v in before.items()}, {k: str(getattr(row, k)) for k in fields})
    if changes:
        await audit.record(
            db,
            actor=actor.actor,
            action="betting.settings_updated",
            resource_type="betting_settings",
            resource_id=actor.tenant.id,
            tenant_id=actor.tenant.id,
            changes=changes,
            meta=meta,
        )
        # Open player portals switch the betting board on or off without a reload.
        publish_after_commit(
            db,
            actor.tenant.id,
            "betting.config_updated",
            {"enabled": row.enabled, "draw_multiplier": str(row.draw_multiplier), "commission_rate": str(row.commission_rate)},
        )
    return row


# ---- wallets --------------------------------------------------------------------------------


async def wallet_for(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False) -> PlayWallet:
    """The player's wallet, created on first use with the tenant's starting grant."""

    async def fetch(locked: bool) -> PlayWallet | None:
        stmt = select(PlayWallet).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id)
        if locked:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(stmt)).scalar_one_or_none()

    wallet = await fetch(lock)
    if wallet is None:
        await db.execute(
            insert(PlayWallet)
            .values(tenant_id=tenant_id, user_id=user_id, balance=ZERO, version=0)
            .on_conflict_do_nothing(constraint="uq_play_wallets_tenant_user")
        )
        # Locking waits for a concurrent creator to commit its grant.
        wallet = await fetch(True)
        assert wallet is not None
    # A wallet without entries has never been granted: grant once, as soon as an administrator has
    # configured a starting balance. The posting key makes it once per player, ever.
    if wallet.version == 0 and (await settings_for(db, tenant_id)).starting_balance > 0:
        wallet = await fetch(True)
        assert wallet is not None
        if await starting_grant(db, wallet):
            await notify_staff(db, tenant_id, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": "grant"})
    return wallet


async def starting_grant(db: AsyncSession, wallet: PlayWallet) -> bool:
    """Post the configured starting balance to a locked wallet that has never had an entry."""
    grant = (await settings_for(db, wallet.tenant_id)).starting_balance
    if wallet.version != 0 or grant <= 0:
        return False
    return await _post(
        db,
        wallet,
        WalletEntryKind.GRANT,
        grant,
        f"grant:{wallet.user_id}",
        memo="Starting Arena Points (configured by an administrator)",
    )


async def _post(
    db: AsyncSession,
    wallet: PlayWallet,
    kind: WalletEntryKind,
    delta: Decimal,
    key: str,
    *,
    bet_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
    memo: str | None = None,
    operation_id: uuid.UUID | None = None,
) -> bool:
    """Apply one posting to a wallet the caller has locked. Returns False if the key was already used."""
    done = (
        await db.execute(
            select(PlayWalletEntry.id).where(
                PlayWalletEntry.tenant_id == wallet.tenant_id, PlayWalletEntry.idempotency_key == key
            )
        )
    ).first()
    if done:
        return False
    balance = wallet.balance + delta
    if balance < 0:
        raise Unprocessable(
            "Not enough Arena Points",
            code="insufficient_balance",
            details={"balance": str(wallet.balance), "requested": str(-delta)},
        )
    wallet.balance = balance
    wallet.version += 1
    db.add(
        PlayWalletEntry(
            # now() is the transaction start; clock_timestamp() keeps entries posted together in order.
            created_at=func.clock_timestamp(),
            tenant_id=wallet.tenant_id,
            wallet_id=wallet.id,
            kind=kind,
            delta=delta,
            balance_after=balance,
            bet_id=bet_id,
            match_id=match_id,
            idempotency_key=key,
            memo=memo,
            operation_id=operation_id,
        )
    )
    await db.flush()
    publish_after_commit(
        db,
        wallet.tenant_id,
        "wallet.updated",
        {"balance": str(balance), "version": wallet.version, "reason": kind.value},
        user_id=wallet.user_id,
    )
    return True


def wallet_out(wallet: PlayWallet) -> WalletOut:
    return WalletOut(balance=wallet.balance, version=wallet.version)


# ---- book snapshots -------------------------------------------------------------------------


async def _book(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID, *, lock: bool = False) -> FightBook | None:
    stmt = select(FightBook).where(FightBook.tenant_id == tenant_id, FightBook.match_id == match_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _window_open(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> bool:
    return bool(
        (
            await db.execute(
                select(window_open_clause(tenant_id))
                .select_from(Match)
                .join(Event, (Event.id == Match.event_id) & (Event.tenant_id == tenant_id))
                .where(Match.tenant_id == tenant_id, Match.id == match_id)
            )
        ).scalar_one_or_none()
    )


async def book_for(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> FightBook | None:
    """Read-only: the fight's book, if betting was ever opened on it."""
    return await _book(db, tenant_id, match_id)


async def window_open(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> bool:
    """Read-only: the fight still takes bets in principle (no result proposed or recorded)."""
    return await _window_open(db, tenant_id, match_id)


async def open_books_in_event(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID) -> list[tuple[uuid.UUID, int]]:
    """(match id, fight number) of every fight of the event whose book is open."""
    rows = await db.execute(
        select(Match.id, Match.match_number)
        .join(FightBook, (FightBook.match_id == Match.id) & (FightBook.tenant_id == tenant_id))
        .where(
            Match.tenant_id == tenant_id,
            Match.event_id == event_id,
            Match.deleted_at.is_(None),
            FightBook.state == BookState.OPEN,
        )
        .order_by(Match.match_number)
    )
    return [(r.id, r.match_number) for r in rows]


def _shown_pools(book: FightBook | None) -> tuple[Decimal, Decimal, Decimal]:
    """Player stakes plus Declarator dummy amounts. The dummy never comes from a balance."""
    if book is None:
        return ZERO, ZERO, ZERO
    return book.meron_pool + book.dummy_meron, book.wala_pool + book.dummy_wala, book.draw_pool


def _quoted(book: FightBook, meron: Decimal, wala: Decimal) -> odds.PoolOdds:
    """The price on the board: a Declarator's pair while one is set, otherwise the 3.66 pool price."""
    if book.meron_odds_override is not None and book.wala_odds_override is not None:
        return odds.PoolOdds(book.meron_odds_override, book.wala_odds_override)
    return odds.pool_odds(meron, wala, book.commission_rate)


def _book_odds(book: FightBook | None) -> odds.PoolOdds:
    if book is None:
        return odds.PoolOdds(None, None)
    meron, wala, _draw = _shown_pools(book)
    if book.state == BookState.OPEN:
        return _quoted(book, meron, wala)
    return odds.PoolOdds(book.final_meron_odds, book.final_wala_odds)


def book_out(match_id: uuid.UUID, book: FightBook | None, settings: BettingSettings, accepting: bool) -> BookOut:
    o = _book_odds(book)
    meron_probability, wala_probability = odds.normalized_probabilities(o.meron, o.wala)
    return BookOut(
        match_id=match_id,
        state=book.state if book else None,
        accepting=accepting,
        pools=Pools(
            meron=(book.meron_pool + book.dummy_meron) if book else ZERO,
            wala=(book.wala_pool + book.dummy_wala) if book else ZERO,
            draw=book.draw_pool if book else ZERO,
        ),
        odds=Odds(
            meron=o.meron,
            wala=o.wala,
            meron_display=odds.display_odds(o.meron),
            wala_display=odds.display_odds(o.wala),
            meron_probability=meron_probability,
            wala_probability=wala_probability,
        ),
        final=book is not None and book.state != BookState.OPEN,
        draw_multiplier=settings.draw_multiplier,
        commission_rate=book.commission_rate if book else settings.commission_rate,
        bet_count=book.bet_count if book else 0,
        outcome=book.outcome if book else None,
        version=book.version if book else 0,
    )


async def _snapshot(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID, book: FightBook | None) -> BookOut:
    settings = await settings_for(db, tenant_id)
    accepting = (
        settings.enabled and book is not None and book.state == BookState.OPEN and await _window_open(db, tenant_id, match_id)
    )
    return book_out(match_id, book, settings, accepting)


def _broadcast(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID, snapshot: BookOut) -> None:
    """Aggregate totals only: no usernames, balances or individual bets leave this function."""
    publish_after_commit(db, tenant_id, "book.updated", {**snapshot.model_dump(mode="json"), "event_id": str(event_id)})


async def _locked_claims(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    """Sum of stake × locked odds on MERON and on WALA. DRAW is not part of the pot."""
    locked = func.round(Bet.amount * Bet.multiplier, 2)
    rows = (
        await db.execute(
            select(Bet.side, func.coalesce(func.sum(locked), 0))
            .where(
                Bet.tenant_id == tenant_id,
                Bet.match_id == match_id,
                Bet.side.in_((BetSide.MERON, BetSide.WALA)),
            )
            .group_by(Bet.side)
        )
    ).all()
    found = {side: Decimal(total) for side, total in rows}
    return found.get(BetSide.MERON, ZERO), found.get(BetSide.WALA, ZERO)


async def _my_bets(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID, user_id: uuid.UUID) -> list[Bet]:
    rows = await db.execute(
        select(Bet)
        .where(Bet.tenant_id == tenant_id, Bet.match_id == match_id, Bet.user_id == user_id)
        .order_by(Bet.created_at, Bet.id)
    )
    return list(rows.scalars())


def position(
    bets: list[Bet], snapshot: BookOut, claims: tuple[Decimal, Decimal] = (ZERO, ZERO)
) -> MyPosition:
    """The caller's own stake and projected (or settled) gross return per side.

    An open MERON or WALA stake is priced at the odds locked on each bet, then limited so the
    winning side cannot be paid more than the pot. ``claims`` is every locked return on
    (MERON, WALA)."""
    pot = snapshot.pools.meron + snapshot.pools.wala
    sides: dict[BetSide, SidePosition] = {}
    for side in BetSide:
        mine = [b for b in bets if b.side == side]
        stake = sum((b.amount for b in mine), ZERO)
        settled = [b for b in mine if b.status != BetStatus.ACCEPTED]
        if mine and len(settled) == len(mine):
            sides[side] = SidePosition(stake=stake, pays=sum((b.payout for b in mine), ZERO), result=mine[0].status)
        elif side == BetSide.DRAW:
            pays = sum((odds.fixed_pays(b.amount, b.multiplier) for b in mine if b.multiplier is not None), ZERO)
            sides[side] = SidePosition(stake=stake, pays=pays, result=None)
        else:
            locked = sum((odds.fixed_pays(b.amount, b.multiplier) for b in mine if b.multiplier is not None), ZERO)
            unpriced = [b for b in mine if b.multiplier is None]
            if unpriced:
                board = snapshot.odds.meron if side == BetSide.MERON else snapshot.odds.wala
                locked += sum((odds.estimated_pays(b.amount, board) or ZERO for b in unpriced), ZERO)
            claim = claims[0] if side == BetSide.MERON else claims[1]
            sides[side] = SidePosition(stake=stake, pays=odds.cap_share(locked, claim, pot), result=None)
    settled_bets = [b for b in bets if b.status != BetStatus.ACCEPTED]
    payout = sum((b.payout for b in settled_bets), ZERO) if bets and len(settled_bets) == len(bets) else None
    return MyPosition(
        meron=sides[BetSide.MERON],
        wala=sides[BetSide.WALA],
        draw=sides[BetSide.DRAW],
        settled_payout=payout,
        settled_net=None if payout is None else payout - sum((b.amount for b in bets), ZERO),
    )


async def _visible_match(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    row = (
        await db.execute(
            select(Match.id, Match.event_id)
            .join(Event, (Event.id == Match.event_id) & (Event.tenant_id == tenant_id))
            .where(
                Match.tenant_id == tenant_id,
                Match.id == match_id,
                Match.deleted_at.is_(None),
                Event.deleted_at.is_(None),
                Event.status.in_(VISIBLE_EVENT_STATUSES),
            )
        )
    ).first()
    if row is None:
        raise NotFound("Match not found")
    return row.id, row.event_id


async def event_pools(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID) -> list[FightPools]:
    """Totals players already see on a fight, for every fight of one visible event."""
    _require_player(actor)
    tenant_id = actor.tenant.id
    visible = (
        await db.execute(
            select(Event.id).where(
                Event.tenant_id == tenant_id,
                Event.id == event_id,
                Event.deleted_at.is_(None),
                Event.status.in_(VISIBLE_EVENT_STATUSES),
            )
        )
    ).first()
    if visible is None:
        raise NotFound("Event not found")
    rows = (
        await db.execute(
            select(Match.id, FightBook.meron_pool, FightBook.dummy_meron, FightBook.wala_pool, FightBook.dummy_wala)
            .join(FightBook, (FightBook.match_id == Match.id) & (FightBook.tenant_id == tenant_id))
            .where(Match.tenant_id == tenant_id, Match.event_id == event_id, Match.deleted_at.is_(None))
            .order_by(Match.match_number)
        )
    ).all()
    return [
        FightPools(match_id=match_id, meron=meron + dummy_meron, wala=wala + dummy_wala)
        for match_id, meron, dummy_meron, wala, dummy_wala in rows
    ]


async def player_book(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID) -> PlayerBookOut:
    tenant_id = actor.tenant.id
    _require_player(actor)
    await _visible_match(db, tenant_id, match_id)
    settings = await settings_for(db, tenant_id)
    snapshot = await _snapshot(db, tenant_id, match_id, await _book(db, tenant_id, match_id))
    wallet = await wallet_for(db, tenant_id, actor.id)
    bets = await _my_bets(db, tenant_id, match_id, actor.id)
    claims = await _locked_claims(db, tenant_id, match_id)
    return PlayerBookOut(
        book=snapshot, mine=position(bets, snapshot, claims), wallet=wallet_out(wallet), limits=_limits(settings, bets)
    )


def _limits(settings: BettingSettings, bets: list[Bet]) -> Limits:
    staked = sum((b.amount for b in bets), ZERO)
    return Limits(
        min_bet=settings.min_bet,
        max_bet=settings.max_bet,
        max_fight_stake=settings.max_fight_stake,
        remaining_fight_stake=max(ZERO, settings.max_fight_stake - staked),
    )


async def config(db: AsyncSession, actor: CurrentUser) -> BettingConfigOut:
    settings = await settings_for(db, actor.tenant.id)
    is_player = actor.user.account_type == AccountType.PLAYER
    wallet = await wallet_for(db, actor.tenant.id, actor.id) if is_player and settings.enabled else None
    return BettingConfigOut(
        enabled=settings.enabled,
        draw_multiplier=settings.draw_multiplier,
        commission_rate=settings.commission_rate,
        min_bet=settings.min_bet,
        max_bet=settings.max_bet,
        max_fight_stake=settings.max_fight_stake,
        wallet=wallet_out(wallet) if wallet else None,
    )


# ---- placing bets ---------------------------------------------------------------------------


def _require_player(actor: CurrentUser) -> None:
    if actor.user.account_type != AccountType.PLAYER:
        raise Forbidden("Only player accounts can place bets", code="players_only")


async def _by_request(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, request_id: uuid.UUID) -> Bet | None:
    return (
        await db.execute(select(Bet).where(Bet.tenant_id == tenant_id, Bet.user_id == user_id, Bet.request_id == request_id))
    ).scalar_one_or_none()


async def _replay(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: BetIn, bet: Bet) -> BetPlaced:
    if (bet.match_id, bet.side, bet.amount) != (match_id, data.side, data.amount):
        raise Conflict("This request key was already used for a different bet", code="idempotency_conflict")
    view = await player_book(db, actor, match_id)
    return BetPlaced(**view.model_dump(), bet=BetOut.model_validate(bet), replayed=True)


async def place_bet(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: BetIn, meta: RequestMeta) -> BetPlaced:
    tenant_id = actor.tenant.id
    _require_player(actor)
    settings = await settings_for(db, tenant_id)
    if not settings.enabled:
        raise Conflict("Betting is not available", code="betting_disabled")
    if (existing := await _by_request(db, tenant_id, actor.id, data.request_id)) is not None:
        return await _replay(db, actor, match_id, data, existing)
    if data.amount < settings.min_bet:
        raise Unprocessable(
            f"The minimum bet is {settings.min_bet:,.2f}", code="bet_below_minimum", details={"min_bet": str(settings.min_bet)}
        )
    if data.amount > settings.max_bet:
        raise Unprocessable(
            f"The maximum bet is {settings.max_bet:,.2f}", code="bet_above_maximum", details={"max_bet": str(settings.max_bet)}
        )

    _, event_id = await _visible_match(db, tenant_id, match_id)
    # Shared locks, event then fight: bets run together but never alongside a close, a result
    # or a cancellation (see the module docstring).
    await db.execute(select(Event.id).where(Event.tenant_id == tenant_id, Event.id == event_id).with_for_update(read=True))
    await db.execute(select(Match.id).where(Match.tenant_id == tenant_id, Match.id == match_id).with_for_update(read=True))
    book = await _book(db, tenant_id, match_id, lock=True)
    if book is None or book.state != BookState.OPEN or not await _window_open(db, tenant_id, match_id):
        raise Conflict("Betting is closed for this fight", code="betting_closed")

    wallet = await wallet_for(db, tenant_id, actor.id, lock=True)
    if (existing := await _by_request(db, tenant_id, actor.id, data.request_id)) is not None:
        return await _replay(db, actor, match_id, data, existing)
    staked = (
        await db.execute(
            select(func.coalesce(func.sum(Bet.amount), 0)).where(
                Bet.tenant_id == tenant_id, Bet.match_id == match_id, Bet.user_id == actor.id
            )
        )
    ).scalar_one()
    remaining = settings.max_fight_stake - Decimal(staked)
    if data.amount > remaining:
        raise Unprocessable(
            f"This bet would exceed the per-fight limit of {settings.max_fight_stake:,.2f}",
            code="fight_limit_reached",
            details={"remaining": str(max(ZERO, remaining))},
        )
    if data.amount > wallet.balance:
        raise Unprocessable(
            "Not enough Arena Points",
            code="insufficient_balance",
            details={"balance": str(wallet.balance), "requested": str(data.amount)},
        )

    fixed = data.side == BetSide.DRAW
    locked = settings.draw_multiplier
    if not fixed:
        meron, wala, _draw = _shown_pools(book)
        if data.side == BetSide.MERON:
            meron += data.amount
        else:
            wala += data.amount
        quoted = _quoted(book, meron, wala).for_side(odds.Side(data.side))
        locked = odds.lock_odds(quoted if quoted is not None else odds.DEFAULT_ODDS)
    # Who earns commission on this bet, at today's rates (later rate changes never apply to it).
    agent_id, master_agent_id, agent_rate, master_rate = await commissions.snapshot(db, tenant_id, actor.user)
    bet = Bet(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        match_id=match_id,
        user_id=actor.id,
        request_id=data.request_id,
        side=data.side,
        amount=data.amount,
        pricing=BetPricing.FIXED if fixed else BetPricing.POOL,
        multiplier=locked,
        status=BetStatus.ACCEPTED,
        payout=ZERO,
        credited=ZERO,
        agent_id=agent_id,
        master_agent_id=master_agent_id,
        agent_rate=agent_rate,
        master_rate=master_rate,
    )
    db.add(bet)
    await db.flush()
    await db.refresh(bet)
    await _post(db, wallet, WalletEntryKind.STAKE, -data.amount, f"stake:{bet.id}", bet_id=bet.id, match_id=match_id)
    column = POOL_COLUMNS[data.side]
    setattr(book, column, getattr(book, column) + data.amount)
    book.bet_count += 1
    book.version += 1
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="bet.placed",
        resource_type="bet",
        resource_id=bet.id,
        tenant_id=tenant_id,
        changes={
            "match_id": match_id,
            "side": data.side,
            "amount": str(data.amount),
            "multiplier": str(bet.multiplier) if bet.multiplier else None,
        },
        meta=meta,
    )
    snapshot = await _snapshot(db, tenant_id, match_id, book)
    _broadcast(db, tenant_id, event_id, snapshot)
    publish_after_commit(db, tenant_id, "bets.updated", {"match_id": str(match_id)}, user_id=actor.id)
    bets = await _my_bets(db, tenant_id, match_id, actor.id)
    claims = await _locked_claims(db, tenant_id, match_id)
    return BetPlaced(
        book=snapshot,
        mine=position(bets, snapshot, claims),
        wallet=wallet_out(wallet),
        limits=_limits(settings, bets),
        bet=BetOut.model_validate(bet),
        replayed=False,
    )


async def add_dummy_stake(
    db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: DummyStakeIn, meta: RequestMeta
) -> BookOut:
    """Add a Declarator amount to MERON or WALA. It changes the pool every player sees, and no balance.

    Allowed while the fight is open or closed, until the result is declared. On a closed fight the
    locked odds are recalculated from the new totals so the figures players see match settlement.
    The caller holds the event and fight locks. Resending ``request_id`` returns the same book."""
    tenant_id = actor.tenant.id
    book = await _book(db, tenant_id, match_id, lock=True)
    if book is None or book.state not in (BookState.OPEN, BookState.CLOSED):
        raise Conflict("Dummy points can be added before this fight's result is declared", code="betting_closed")
    if data.request_id in set(book.dummy_request_ids or []):
        snapshot = await _snapshot(db, tenant_id, match_id, book)
        return snapshot
    # A dummy is not a player's bet and moves no balance, so the per-bet player limits do not apply.
    # The amount is still validated (> 0, 2 decimals) by DummyStakeIn.
    side = BetSide(data.side)
    column = "dummy_meron" if side == BetSide.MERON else "dummy_wala"
    setattr(book, column, getattr(book, column) + data.amount)
    book.dummy_request_ids = [*(book.dummy_request_ids or []), data.request_id]
    if book.state == BookState.CLOSED:
        _lock_prices(book)
    book.version += 1
    await db.flush()
    event_id = (await db.execute(select(Match.event_id).where(Match.tenant_id == tenant_id, Match.id == match_id))).scalar_one()
    await audit.record(
        db,
        actor=actor.actor,
        action="betting.dummy_added",
        resource_type="match",
        resource_id=match_id,
        tenant_id=tenant_id,
        changes={"side": data.side, "amount": str(data.amount), "request_id": str(data.request_id)},
        meta=meta,
    )
    snapshot = await _snapshot(db, tenant_id, match_id, book)
    _broadcast(db, tenant_id, event_id, snapshot)
    return snapshot


def _odds_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


async def set_odds(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: OddsUpdate, meta: RequestMeta) -> BookOut:
    """Put a Declarator's MERON/WALA price on an open fight, or return the fight to the pool price.

    The caller holds the event and fight locks. Betting must still be open. Accepted bets keep
    the odds they locked; only the board, and bets placed after this, use the new price."""
    tenant_id = actor.tenant.id
    book = await _book(db, tenant_id, match_id, lock=True)
    if book is None or book.state != BookState.OPEN:
        raise Conflict("Odds can be changed only while betting is open", code="betting_closed")
    previous = (book.meron_odds_override, book.wala_odds_override)
    if data.reset:
        book.meron_odds_override = None
        book.wala_odds_override = None
    else:
        if data.meron is None or data.wala is None:
            raise Unprocessable("Enter both Meron and Wala odds, or reset to the pool price.", code="odds_required")
        try:
            quoted = odds.manual_odds(data.meron, data.wala)
        except ValueError as exc:
            raise Unprocessable(str(exc), code="odds_invalid") from exc
        book.meron_odds_override = quoted.meron
        book.wala_odds_override = quoted.wala
    book.version += 1
    await db.flush()
    event_id = (await db.execute(select(Match.event_id).where(Match.tenant_id == tenant_id, Match.id == match_id))).scalar_one()
    await audit.record(
        db,
        actor=actor.actor,
        action="betting.odds_set",
        resource_type="match",
        resource_id=match_id,
        tenant_id=tenant_id,
        changes={
            "meron": [_odds_text(previous[0]), _odds_text(book.meron_odds_override)],
            "wala": [_odds_text(previous[1]), _odds_text(book.wala_odds_override)],
        },
        meta=meta,
    )
    snapshot = await _snapshot(db, tenant_id, match_id, book)
    _broadcast(db, tenant_id, event_id, snapshot)
    return snapshot


async def my_bets(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    match_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    status: BetStatus | None = None,
) -> Page[BetHistoryItem]:
    tenant_id = actor.tenant.id
    stmt = (
        select(Bet, Match.event_id, Match.match_number, Event.name)
        .join(Match, (Match.id == Bet.match_id) & (Match.tenant_id == tenant_id))
        .join(Event, (Event.id == Match.event_id) & (Event.tenant_id == tenant_id))
        # Only ever the caller's own bets.
        .where(Bet.tenant_id == tenant_id, Bet.user_id == actor.id)
    )
    if match_id:
        stmt = stmt.where(Bet.match_id == match_id)
    if event_id:
        stmt = stmt.where(Match.event_id == event_id)
    if status:
        stmt = stmt.where(Bet.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Bet.created_at.desc(), Bet.id.desc()).offset(params.offset).limit(params.page_size))
    ).all()
    items = [
        BetHistoryItem(**BetOut.model_validate(b).model_dump(), event_id=ev, match_number=num, event_name=name)
        for b, ev, num, name in rows
    ]
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)


# ---- opening and closing --------------------------------------------------------------------


async def _lock_fight(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> tuple[Event, Match]:
    from app.modules.matches.service import get_match
    from app.shared.repository import get_scoped

    candidate = await get_match(db, tenant_id, match_id)
    event = await get_scoped(db, Event, candidate.event_id, tenant_id, for_update=True)
    match = await get_match(db, tenant_id, match_id, for_update=True)
    assert event is not None
    return event, match


def _lock_prices(book: FightBook) -> None:
    """Store the board price. A manual pair is kept; otherwise the pools are priced again."""
    meron, wala, _draw = _shown_pools(book)
    board = _quoted(book, meron, wala)
    places = odds.ODDS_PLACES
    book.final_meron_odds = None if board.meron is None else board.meron.quantize(places, rounding=ROUND_DOWN)
    book.final_wala_odds = None if board.wala is None else board.wala.quantize(places, rounding=ROUND_DOWN)


def _freeze(book: FightBook) -> None:
    """Close betting and lock the MERON/WALA price then on the board. Accepted bets keep their own odds."""
    book.state = BookState.CLOSED
    book.closed_at = utcnow()
    _lock_prices(book)
    book.version += 1


async def open_betting(
    db: AsyncSession,
    actor: CurrentUser,
    match_id: uuid.UUID,
    meta: RequestMeta,
    *,
    by: str = "operator",
    supersede_reason: str | None = None,
) -> AdminBookOut:
    """Open a fight's book. A closed fight stays closed until its result is declared.

    Only one fight per event may take bets at a time.

    The rule is checked after the event row is locked ``FOR UPDATE`` (``_lock_fight``), which every
    open, close, result and cancellation of the event takes first, so concurrent opens of two fights
    serialise and the second one sees the first book open. ``supersede_reason`` is the audited
    emergency path for event managers: it closes the other open book(s) of the event first instead
    of refusing; the invariant still holds afterwards."""
    tenant_id = actor.tenant.id
    settings = await settings_for(db, tenant_id)
    if not settings.enabled:
        raise Conflict("Enable betting in settings first", code="betting_disabled")
    event, match = await _lock_fight(db, tenant_id, match_id)
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Betting opens on fights of a live event", code="event_not_live")
    if match.status not in (MatchStatus.SCHEDULED, MatchStatus.IN_PROGRESS) or not await _window_open(db, tenant_id, match.id):
        raise Unprocessable("Betting cannot open on a decided fight or one with a pending result", code="invalid_match_state")
    book = await _book(db, tenant_id, match.id, lock=True)
    if book is not None and book.state == BookState.SETTLED:
        raise Conflict("This fight's bets are already settled", code="book_settled")
    if book is not None and book.state == BookState.CLOSED:
        raise Conflict(
            "Betting is closed. Declare Meron, Wala, or Draw before opening betting again.",
            code="result_required",
        )
    if book is not None and book.state == BookState.OPEN:
        return await admin_book(db, tenant_id, match.id)
    elsewhere = [(mid, n) for mid, n in await open_books_in_event(db, tenant_id, event.id) if mid != match.id]
    if elsewhere and supersede_reason is None:
        raise Conflict(
            f"Betting is still open on Fight #{elsewhere[0][1]}. Close it first.",
            code="betting_open_elsewhere",
            details={"match_number": elsewhere[0][1]},
        )
    for other_id, other_number in elsewhere:
        other = await _book(db, tenant_id, other_id, lock=True)
        if other is not None and other.state == BookState.OPEN:
            await _close(db, actor, event.id, other, "superseded", meta)
            await audit.record(
                db,
                actor=actor.actor,
                action="betting.superseded",
                resource_type="match",
                resource_id=other_id,
                tenant_id=tenant_id,
                changes={"match_number": other_number, "replaced_by": match.match_number, "reason": supersede_reason},
                meta=meta,
            )
    if book is None:
        book = FightBook(
            tenant_id=tenant_id,
            match_id=match.id,
            state=BookState.OPEN,
            commission_rate=settings.commission_rate,
            opened_at=utcnow(),
            version=1,
        )
        db.add(book)
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="betting.opened",
        resource_type="match",
        resource_id=match.id,
        tenant_id=tenant_id,
        changes={
            "by": by,
            "reopened": False,
            "match_number": match.match_number,
            "commission_rate": str(book.commission_rate),
        },
        meta=meta,
    )
    _broadcast(db, tenant_id, event.id, await _snapshot(db, tenant_id, match.id, book))
    return await admin_book(db, tenant_id, match.id)


async def close_betting(
    db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, meta: RequestMeta, *, by: str = "operator"
) -> AdminBookOut:
    tenant_id = actor.tenant.id
    event, match = await _lock_fight(db, tenant_id, match_id)
    book = await _book(db, tenant_id, match.id, lock=True)
    if book is None:
        raise Conflict("Betting was never opened for this fight", code="book_not_open")
    if book.state == BookState.OPEN:
        await _close(db, actor, event.id, book, by, meta)
    return await admin_book(db, tenant_id, match.id)


async def _close(
    db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, book: FightBook, why: str, meta: RequestMeta | None
) -> None:
    _freeze(book)
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="betting.closed",
        resource_type="match",
        resource_id=book.match_id,
        tenant_id=book.tenant_id,
        changes={
            "by": why,
            "pools": {"meron": str(book.meron_pool), "wala": str(book.wala_pool), "draw": str(book.draw_pool)},
            "final_odds": {"meron": str(book.final_meron_odds), "wala": str(book.final_wala_odds)},
        },
        meta=meta,
    )
    _broadcast(db, book.tenant_id, event_id, await _snapshot(db, book.tenant_id, book.match_id, book))
    _announce_closed(db, event_id, book)


def _announce_closed(db: AsyncSession, event_id: uuid.UUID, book: FightBook) -> None:
    """``betting.closed``: last call, with the frozen pools and locked multipliers (aggregates only)."""
    publish_after_commit(
        db,
        book.tenant_id,
        "betting.closed",
        {
            "match_id": str(book.match_id),
            "event_id": str(event_id),
            "pools": {"meron": str(book.meron_pool), "wala": str(book.wala_pool), "draw": str(book.draw_pool)},
            "final_odds": {
                "meron": None if book.final_meron_odds is None else str(book.final_meron_odds),
                "wala": None if book.final_wala_odds is None else str(book.final_wala_odds),
            },
            "version": book.version,
        },
    )


async def close_for_declaration(db: AsyncSession, actor: CurrentUser, match: Match, meta: RequestMeta) -> None:
    """A submitted result ends betting. The caller holds the event and fight locks."""
    book = await _book(db, match.tenant_id, match.id, lock=True)
    if book is not None and book.state == BookState.OPEN:
        await _close(db, actor, match.event_id, book, "declaration", meta)


# ---- settlement -----------------------------------------------------------------------------


async def _official_outcome(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> tuple[str, int | None] | None:
    # Column reads, not ORM objects: an event cancellation updates fights in bulk.
    status, deleted_at = (
        await db.execute(select(Match.status, Match.deleted_at).where(Match.tenant_id == tenant_id, Match.id == match_id))
    ).one()
    if status == MatchStatus.CANCELLED or deleted_at is not None:
        return "cancelled", None
    row = (
        await db.execute(
            select(MatchResult.outcome, MatchResult.revision).where(
                MatchResult.tenant_id == tenant_id, MatchResult.match_id == match_id
            )
        )
    ).first()
    return (row.outcome.value, row.revision) if row else None


async def settle(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, meta: RequestMeta | None = None) -> None:
    """Settle (or re-settle after a correction) a fight from its official record.

    Every caller already holds the event and fight ``FOR UPDATE``: result declaration and
    correction, fight cancellation and deletion, and event cancellation."""
    tenant_id = actor.tenant.id
    book = await _book(db, tenant_id, match_id, lock=True)
    if book is None:
        return
    official = await _official_outcome(db, tenant_id, match_id)
    if official is None:
        return
    outcome, revision = official
    event_id = (await db.execute(select(Match.event_id).where(Match.tenant_id == tenant_id, Match.id == match_id))).scalar_one()
    if book.state == BookState.OPEN:
        _freeze(book)
        _announce_closed(db, event_id, book)

    bets = list(
        (
            await db.execute(
                select(Bet)
                .where(Bet.tenant_id == tenant_id, Bet.match_id == match_id)
                .order_by(Bet.created_at, Bet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    lines = [odds.BetLine(b.id, b.side, b.amount, b.multiplier) for b in bets]
    # Dummy lines take a share of a win and are never posted to a wallet.
    if book.dummy_meron > 0:
        lines.append(odds.BetLine("dummy:meron", odds.Side.MERON, book.dummy_meron))
    if book.dummy_wala > 0:
        lines.append(odds.BetLine("dummy:wala", odds.Side.WALA, book.dummy_wala))
    result = odds.settle(lines, outcome, book.commission_rate)
    shown = _shown_pools(book)
    if (result.meron_pool, result.wala_pool, result.draw_pool) != shown:
        log.error("book_pool_mismatch", match_id=str(match_id), book=str(shown[0]), bets=str(result.meron_pool))

    wallets = {
        w.user_id: w
        for w in (
            await db.execute(
                select(PlayWallet)
                .where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id.in_({b.user_id for b in bets}))
                .order_by(PlayWallet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    now = utcnow()
    round_no = book.settle_count + 1
    changed = False
    shortfall = ZERO
    for b in bets:
        verdict, payout = result.verdicts[b.id]
        status = BetStatus(verdict.value)
        delta = payout - b.credited
        if delta != 0:
            wallet = wallets[b.user_id]
            if delta < 0 and -delta > wallet.balance:
                # A corrected result reclaims a payout that has partly been staked again: take
                # what is there and record the rest, never a negative balance.
                shortfall += -delta - wallet.balance
                delta = -wallet.balance
            if delta != 0:
                if b.status != BetStatus.ACCEPTED:
                    kind = WalletEntryKind.RESETTLEMENT
                elif verdict == odds.Verdict.REFUNDED:
                    kind = WalletEntryKind.REFUND
                else:
                    kind = WalletEntryKind.PAYOUT
                await _post(db, wallet, kind, delta, f"settle:{b.id}:{round_no}", bet_id=b.id, match_id=match_id, memo=outcome)
                b.credited += delta
        if (b.status, b.payout) != (status, payout):
            changed = True
            b.status, b.payout, b.net_profit, b.settled_at = status, payout, payout - b.amount, now

    # Agent earnings follow the official result: posted, left alone on a repeat, reversed on a
    # correction. Earnings never touch a wallet.
    await commissions.sync_fight(db, actor, match_id, outcome, revision, bets, meta)

    first = book.state != BookState.SETTLED
    if first or changed or book.outcome != outcome or book.result_revision != revision:
        book.state = BookState.SETTLED
        book.outcome = outcome
        book.result_revision = revision
        book.commission_amount = result.commission
        book.final_meron_odds, book.final_wala_odds = result.meron_odds, result.wala_odds
        book.settled_at = now
        book.settle_count = round_no
        book.version += 1
        await db.flush()
        stakes = result.meron_pool + result.wala_pool + result.draw_pool
        payouts = sum((b.payout for b in bets), ZERO)
        await audit.record(
            db,
            actor=actor.actor,
            action="betting.settled" if first else "betting.resettled",
            resource_type="match",
            resource_id=match_id,
            tenant_id=tenant_id,
            changes={
                "outcome": outcome,
                "revision": revision,
                "bets": len(bets),
                "stakes": str(stakes),
                "payouts": str(payouts),
                "commission": str(result.commission),
                "uncollected": str(shortfall),
            },
            meta=meta,
        )
        _broadcast(db, tenant_id, event_id, await _snapshot(db, tenant_id, match_id, book))
        # Fight-level totals for every dashboard; each player's own figures follow on their channel.
        refunds = sum((b.payout for b in bets if b.status == BetStatus.REFUNDED), ZERO)
        publish_after_commit(
            db,
            tenant_id,
            "fight.settled",
            {
                "match_id": str(match_id),
                "event_id": str(event_id),
                "outcome": outcome,
                "revision": revision,
                "resettled": not first,
                "stakes": str(stakes),
                "payouts": str(payouts - refunds),
                "refunds": str(refunds),
                "commission": str(result.commission),
                "version": book.version,
            },
        )
        await notify_staff(db, tenant_id, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": "settlement"})
        for user_id in sorted({b.user_id for b in bets}):
            publish_after_commit(db, tenant_id, "bets.settled", {"match_id": str(match_id), "outcome": outcome}, user_id=user_id)


async def settle_event(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, meta: RequestMeta | None = None) -> None:
    match_ids = (
        await db.execute(
            select(FightBook.match_id)
            .join(Match, (Match.id == FightBook.match_id) & (Match.tenant_id == actor.tenant.id))
            .where(FightBook.tenant_id == actor.tenant.id, Match.event_id == event_id)
            .order_by(FightBook.match_id)
        )
    ).scalars()
    for match_id in list(match_ids):
        await settle(db, actor, match_id, meta)


# ---- administration -------------------------------------------------------------------------


def _admin_out(match: Match, book: FightBook | None, snapshot: BookOut) -> AdminBookOut:
    return AdminBookOut(
        **snapshot.model_dump(),
        event_id=match.event_id,
        match_number=match.match_number,
        commission_amount=book.commission_amount if book else None,
        final_meron_odds=book.final_meron_odds if book else None,
        final_wala_odds=book.final_wala_odds if book else None,
        opened_at=book.opened_at if book else None,
        closed_at=book.closed_at if book else None,
        settled_at=book.settled_at if book else None,
    )


async def admin_book(db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID) -> AdminBookOut:
    from app.modules.matches.service import get_match

    match = await get_match(db, tenant_id, match_id)
    book = await _book(db, tenant_id, match_id)
    return _admin_out(match, book, await _snapshot(db, tenant_id, match_id, book))


async def event_books(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID) -> list[AdminBookOut]:
    rows = (
        await db.execute(
            select(Match, FightBook)
            .join(FightBook, (FightBook.match_id == Match.id) & (FightBook.tenant_id == tenant_id))
            .where(Match.tenant_id == tenant_id, Match.event_id == event_id, Match.deleted_at.is_(None))
            .order_by(Match.match_number)
        )
    ).all()
    return [_admin_out(m, b, await _snapshot(db, tenant_id, m.id, b)) for m, b in rows]
