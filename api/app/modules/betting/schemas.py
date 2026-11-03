import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field

from app.modules.betting.models import BetPricing, BetSide, BetStatus, BookState
from app.shared.schemas import Schema

DISCLAIMER = (
    "Arena Points are for simulated play only. They have no cash value and cannot be bought, transferred, "
    "withdrawn, redeemed or exchanged for money."
)

Amount = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]


class BettingSettingsOut(Schema):
    enabled: bool
    commission_rate: Decimal
    draw_multiplier: Decimal
    min_bet: Decimal
    max_bet: Decimal
    max_fight_stake: Decimal
    starting_balance: Decimal


class BettingSettingsUpdate(Schema):
    enabled: bool | None = None
    commission_rate: Annotated[Decimal, Field(ge=0, lt=Decimal("0.5"), max_digits=5, decimal_places=4)] | None = None
    draw_multiplier: Annotated[Decimal, Field(ge=1, le=100, max_digits=6, decimal_places=2)] | None = None
    min_bet: Amount | None = None
    max_bet: Amount | None = None
    max_fight_stake: Amount | None = None
    starting_balance: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)] | None = None


class Pools(Schema):
    meron: Decimal
    wala: Decimal
    draw: Decimal


class Odds(Schema):
    """Unrounded multipliers (``None`` while a side has no stake) and the 2 dp display values."""

    meron: Decimal | None
    wala: Decimal | None
    meron_display: Decimal | None
    wala_display: Decimal | None


class BookOut(Schema):
    """What every viewer of a fight sees: aggregate totals only, never individual bets."""

    match_id: uuid.UUID
    state: BookState | None  # None: betting was never opened for this fight
    accepting: bool
    pools: Pools
    odds: Odds
    final: bool  # odds are locked (betting closed) rather than provisional
    draw_multiplier: Decimal  # applies to DRAW bets accepted now
    commission_rate: Decimal
    bet_count: int
    outcome: str | None
    version: int


class SidePosition(Schema):
    stake: Decimal
    pays: Decimal | None  # projected gross return; None while the odds are unavailable
    result: BetStatus | None  # set once settled


class MyPosition(Schema):
    meron: SidePosition
    wala: SidePosition
    draw: SidePosition
    settled_payout: Decimal | None
    settled_net: Decimal | None


class Limits(Schema):
    min_bet: Decimal
    max_bet: Decimal
    max_fight_stake: Decimal
    remaining_fight_stake: Decimal


class WalletOut(Schema):
    balance: Decimal
    version: int
    disclaimer: str = DISCLAIMER


class PlayerBookOut(Schema):
    book: BookOut
    mine: MyPosition
    wallet: WalletOut
    limits: Limits


class BettingConfigOut(Schema):
    enabled: bool
    draw_multiplier: Decimal
    commission_rate: Decimal
    min_bet: Decimal
    max_bet: Decimal
    max_fight_stake: Decimal
    wallet: WalletOut | None
    disclaimer: str = DISCLAIMER


class BetIn(Schema):
    side: BetSide
    amount: Amount
    request_id: uuid.UUID = Field(description="Client-generated key; resending it never places a second bet.")


class BetOut(Schema):
    id: uuid.UUID
    match_id: uuid.UUID
    side: BetSide
    amount: Decimal
    pricing: BetPricing
    multiplier: Decimal | None
    status: BetStatus
    payout: Decimal
    net_profit: Decimal | None
    created_at: datetime
    settled_at: datetime | None


class BetPlaced(PlayerBookOut):
    bet: BetOut
    replayed: bool


class BetHistoryItem(BetOut):
    event_id: uuid.UUID
    event_name: str
    match_number: int


class OpenBettingIn(Schema):
    """Emergency override (event managers only): close any other open fight of the event first.

    Without it, opening a second fight of an event is refused with ``betting_open_elsewhere``."""

    supersede: bool = False
    reason: str | None = Field(default=None, max_length=300)


class AdminBookOut(BookOut):
    event_id: uuid.UUID
    match_number: int
    commission_amount: Decimal | None
    final_meron_odds: Decimal | None
    final_wala_odds: Decimal | None
    opened_at: datetime | None
    closed_at: datetime | None
    settled_at: datetime | None


# ---- staff views: declarator monitor and admin overview ----


class Obligations(Schema):
    """Points each result would pay back to players, stakes included (refunds count too)."""

    meron: Decimal
    wala: Decimal
    draw: Decimal
    cancelled: Decimal


class SettlementSummary(Schema):
    outcome: str
    revision: int | None
    stakes: Decimal
    paid_to_winners: Decimal
    refunds: Decimal
    commission: Decimal
    uncollected: Decimal  # payouts reclaimed by a correction that exceeded the player's balance
    settled_at: datetime | None


class FightBookSummary(BookOut):
    event_id: uuid.UUID
    event_name: str
    match_number: int
    match_status: str
    stakes_total: Decimal
    obligations: Obligations | None  # while bets are unsettled
    settlement: SettlementSummary | None
    opened_at: datetime | None
    closed_at: datetime | None


class WalletRow(Schema):
    user_id: uuid.UUID
    username: str
    full_name: str | None
    balance: Decimal
    granted: Decimal
    staked: Decimal
    returned: Decimal  # payouts, refunds and resettlements credited back
    open_stakes: Decimal  # accepted bets not yet settled
    entries: int
    updated_at: datetime


class WalletEntryRow(Schema):
    id: uuid.UUID
    created_at: datetime
    user_id: uuid.UUID
    username: str
    kind: str
    delta: Decimal
    balance_after: Decimal
    reference: str
    memo: str | None
    match_id: uuid.UUID | None
    match_number: int | None
    event_name: str | None


class ReconciliationChecks(Schema):
    wallets_match_ledger: bool
    wallet_mismatches: int
    pool_mismatches: int
    uncollected_bets: int
    uncollected_amount: Decimal


class Reconciliation(Schema):
    """Where every play point is. ``granted = in_wallets + at_stake + house_result`` always holds;
    the checks recompute balances and pools from the underlying records."""

    granted: Decimal
    grants: int
    staked: Decimal
    paid_to_winners: Decimal
    refunded: Decimal
    resettled: Decimal
    in_wallets: Decimal
    wallets: int
    at_stake: Decimal
    house_result: Decimal  # deductions plus lost stakes, less DRAW winnings above stake
    commission: Decimal
    checks: ReconciliationChecks
    balanced: bool
    disclaimer: str = DISCLAIMER
