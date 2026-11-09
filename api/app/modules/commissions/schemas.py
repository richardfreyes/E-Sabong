import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from app.shared.schemas import Schema

# Rates are fractions: 0.0300 is 3%.
Rate = Annotated[Decimal, Field(ge=0, le=Decimal("1"), max_digits=6, decimal_places=4)]


class CommissionOverview(Schema):
    role: Literal["master_agent", "agent"]
    # Master agent: their total allocation. Agent: the rate their master agent assigned.
    rate: Decimal
    allocation: Decimal | None = None
    upline_rate: Decimal | None = None
    total_earned: Decimal
    this_month: Decimal
    last_month: Decimal
    # Master agent: everything their network earned (agents' shares plus their own). Agent: own.
    network_total: Decimal
    network_this_month: Decimal
    network_last_month: Decimal
    # Lifetime earnings still on the commission ledger, minus amounts already moved to the play wallet.
    available_earnings: Decimal
    # The caller's own play wallet. Separate from the network total shown as Main Wallet.
    own_balance: Decimal


class AgentRateIn(Schema):
    rate: Rate


class AgentRateRow(Schema):
    agent_id: uuid.UUID
    username: str
    full_name: str
    status: str
    players: int
    agent_rate: Decimal
    master_rate: Decimal
    allocation: Decimal
    agent_earned: Decimal
    master_earned: Decimal


class MasterAllocationIn(Schema):
    allocation: Rate


class MasterAllocation(Schema):
    master_agent_id: uuid.UUID
    username: str
    allocation: Decimal
    # True while no rate was saved for this master agent: the 8.5% default applies.
    is_default: bool = False
    # The highest rate of a direct agent: the allocation cannot go below it.
    highest_agent_rate: Decimal = Decimal("0")


class ApproverOut(Schema):
    id: uuid.UUID
    username: str
    full_name: str
    account_type: Literal["loader", "admin"]


class BalanceRequestIn(Schema):
    model_config = {"extra": "forbid", "str_strip_whitespace": True, "from_attributes": True}
    amount: Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), max_digits=9, decimal_places=2)]
    # request adds play balance. cashout takes the main wallet down. earnings takes commission down.
    purpose: Literal["request", "cashout", "earnings"] = "request"
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class BalanceRequestOut(Schema):
    id: uuid.UUID
    created_at: datetime
    reference: str
    requester_id: uuid.UUID
    requester_username: str
    amount: Decimal
    purpose: Literal["request", "cashout", "earnings"]
    status: Literal["pending", "approved", "rejected"]
    decided_at: datetime | None = None
    replayed: bool = False


class DecisionIn(Schema):
    model_config = {"extra": "forbid"}
    decision: Literal["approve", "reject"]


class LoadIn(Schema):
    model_config = {"extra": "forbid", "str_strip_whitespace": True, "from_attributes": True}
    method: Literal["send", "withdraw", "commission"]
    recipient_id: uuid.UUID
    amount: Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), max_digits=9, decimal_places=2)]
    details: Annotated[str, Field(max_length=300)] | None = None
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class LoadOut(Schema):
    method: Literal["send", "withdraw", "commission"]
    recipient_id: uuid.UUID
    recipient_username: str
    amount: Decimal
    balance_after: Decimal
    replayed: bool = False


class EarningsMoveIn(Schema):
    model_config = {"extra": "forbid", "str_strip_whitespace": True, "from_attributes": True}
    amount: Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), max_digits=9, decimal_places=2)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class EarningsMoveOut(Schema):
    id: uuid.UUID
    created_at: datetime
    reference: str
    amount: Decimal
    earnings_before: Decimal
    earnings_after: Decimal
    wallet_after: Decimal
    kind: Literal["merge", "withdrawal"] = "merge"
    status: Literal["applied"] = "applied"
    replayed: bool = False


class HistoryRow(Schema):
    id: uuid.UUID
    created_at: datetime
    match_id: uuid.UUID
    match_number: int
    event_name: str
    # None for a master agent viewing a bet by one of its agents' players: those players belong to
    # the agent, so the master agent sees the agent and the amounts, never the player.
    player_id: uuid.UUID | None
    player_username: str | None
    agent_id: uuid.UUID | None
    agent_username: str | None
    side: str
    bet_amount: Decimal
    agent_rate: Decimal
    agent_points: Decimal
    master_rate: Decimal | None
    master_points: Decimal | None
    outcome: str
    result_revision: int | None
    status: str


class BreakdownRow(Schema):
    key: str
    label: str
    bets: int
    eligible_points: Decimal
    agent_points: Decimal
    master_points: Decimal | None
