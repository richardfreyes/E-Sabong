import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from app.shared.schemas import Schema

# Rates are fractions: 0.0300 is 3%.
Rate = Annotated[Decimal, Field(ge=0, le=Decimal("0.5"), max_digits=6, decimal_places=4)]


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


class HistoryRow(Schema):
    id: uuid.UUID
    created_at: datetime
    match_id: uuid.UUID
    match_number: int
    event_name: str
    player_id: uuid.UUID
    player_username: str
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
