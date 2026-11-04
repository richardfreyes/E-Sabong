import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from app.modules.betting.models import BetPricing, BetSide, BetStatus, BookState, ResetPolicy
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
    reset_policy: ResetPolicy


class BettingSettingsUpdate(Schema):
    enabled: bool | None = None
    commission_rate: Annotated[Decimal, Field(ge=0, lt=Decimal("0.5"), max_digits=5, decimal_places=4)] | None = None
    draw_multiplier: Annotated[Decimal, Field(ge=1, le=100, max_digits=6, decimal_places=2)] | None = None
    min_bet: Amount | None = None
    max_bet: Amount | None = None
    max_fight_stake: Amount | None = None
    starting_balance: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)] | None = None
    # Whether an administrator may reset every participant to the starting balance (never automatic).
    reset_policy: ResetPolicy | None = None


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


class DummyStakeIn(Schema):
    """Pool amount added by a Declarator. It is not a player's bet and moves no balance."""

    side: Literal["meron", "wala"]
    amount: Amount
    request_id: uuid.UUID = Field(description="Client-generated key; resending it never adds the amount twice.")


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


class PlayerStake(Schema):
    """One player's stakes on a fight, summed per side. Amounts are what they bet, not the payout."""

    username: str
    full_name: str | None = None
    meron: Decimal
    wala: Decimal
    draw: Decimal


class FightBookSummary(BookOut):
    event_id: uuid.UUID
    event_name: str
    match_number: int
    match_status: str
    stakes_total: Decimal
    players: list[PlayerStake] = Field(default_factory=list)
    dummy_meron: Decimal = Decimal("0.00")
    dummy_wala: Decimal = Decimal("0.00")
    obligations: Obligations | None  # while bets are unsettled
    settlement: SettlementSummary | None
    opened_at: datetime | None
    closed_at: datetime | None


class WalletRow(Schema):
    user_id: uuid.UUID
    username: str
    full_name: str | None
    balance: Decimal
    granted: Decimal  # starting, uniform and account grants
    adjusted: Decimal = Decimal("0.00")  # net of administrator resets and corrections
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
    # The bet behind a stake/payout/refund/correction row, so a statement shows each bet's outcome
    # (a lost bet adds no ledger row of its own: its stake row reads "lost").
    bet_id: uuid.UUID | None = None
    bet_side: str | None = None
    bet_status: str | None = None
    bet_amount: Decimal | None = None
    bet_credited: Decimal | None = None
    # Administrator balance management rows carry their operation (transaction reference etc.).
    balance_before: Decimal | None = None
    operation_id: uuid.UUID | None = None
    operation_reference: str | None = None
    operation_kind: str | None = None
    operation_status: str | None = None
    operation_remarks: str | None = None


class StatementSummary(Schema):
    """One player's play-balance statement totals (simulated, no cash value).

    Ledger totals come from ``play_wallet_entries`` (authoritative); bet counts from ``bets``.
    ``net_result`` = winnings credited + refunds + corrections - staked: play results only. Administrator
    balance management (account grants, uniform grants, resets, balance corrections) is reported
    separately, so ``balance`` = granted + account_grants + uniform_grants + resets +
    balance_corrections + net_result for an all-time statement (open stakes are already deducted
    through ``staked``)."""

    user_id: uuid.UUID
    username: str
    balance: Decimal
    granted: Decimal
    staked: Decimal
    winnings_credited: Decimal
    refunds: Decimal
    corrections: Decimal
    net_result: Decimal
    bets: int
    bets_won: int
    bets_lost: int
    bets_refunded: int
    bets_open: int
    lost_stakes: Decimal
    open_stakes: Decimal
    entries: int
    account_grants: Decimal = Decimal("0.00")
    uniform_grants: Decimal = Decimal("0.00")
    resets: Decimal = Decimal("0.00")
    balance_corrections: Decimal = Decimal("0.00")


class ReconciliationChecks(Schema):
    wallets_match_ledger: bool
    wallet_mismatches: int
    pool_mismatches: int
    uncollected_bets: int
    uncollected_amount: Decimal


class Reconciliation(Schema):
    """Where every play point is. ``granted + account_granted + uniform_granted + reset_net +
    corrections_net = in_wallets + at_stake + house_result`` always holds; the checks recompute
    balances and pools from the underlying records."""

    granted: Decimal
    grants: int
    staked: Decimal
    paid_to_winners: Decimal
    refunded: Decimal
    resettled: Decimal
    account_granted: Decimal = Decimal("0.00")
    uniform_granted: Decimal = Decimal("0.00")
    reset_net: Decimal = Decimal("0.00")  # resets can move balances down as well as up
    corrections_net: Decimal = Decimal("0.00")
    in_wallets: Decimal
    wallets: int
    at_stake: Decimal
    house_result: Decimal  # deductions plus lost stakes, less DRAW winnings above stake
    commission: Decimal
    checks: ReconciliationChecks
    balanced: bool
    disclaimer: str = DISCLAIMER
