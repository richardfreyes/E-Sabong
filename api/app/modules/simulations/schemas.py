import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.modules.betting.schemas import WalletOut
from app.modules.simulations.access import TournamentRole
from app.modules.simulations.models import SimulationOutcome, SimulationSide, SimulationState
from app.shared.schemas import Schema

DISCLAIMER = "Arena Points are non-purchasable, non-transferable and non-redeemable. They have no cash value."


class SimulationCapabilities(Schema):
    role: TournamentRole | None
    permissions: list[str]
    states: list[SimulationState] = list(SimulationState)
    engine_enabled: bool = True
    disclaimer: str = DISCLAIMER


class ArenaCreate(Schema):
    name: str = Field(min_length=1, max_length=120)


class ArenaOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    enabled: bool
    created_at: datetime


class ArenaUpdate(Schema):
    enabled: bool


class MatchCreate(Schema):
    number: int = Field(gt=0)
    team_a: str = Field(min_length=1, max_length=120)
    team_b: str = Field(min_length=1, max_length=120)
    simulation_fee_rate: Decimal = Field(default=Decimal("0.05"), ge=0, lt=Decimal("0.5"), decimal_places=4)


class TransitionIn(Schema):
    request_id: uuid.UUID
    expected_version: int = Field(ge=0)
    new_state: SimulationState
    outcome: SimulationOutcome | None = None
    final_call_seconds: int | None = Field(default=None, ge=1, le=300)

    @field_validator("new_state", mode="before")
    @classmethod
    def normalize_state(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("outcome", mode="before")
    @classmethod
    def normalize_outcome(cls, value: object) -> object:
        if isinstance(value, str):
            return {"side_a": "team_a", "side_b": "team_b"}.get(value.lower(), value.lower())
        return value

    @model_validator(mode="after")
    def check_payload(self) -> "TransitionIn":
        if (self.outcome is not None) != (self.new_state == SimulationState.RESULT_DECLARED):
            raise ValueError("Supply an outcome only when declaring the result")
        if self.final_call_seconds is not None and self.new_state != SimulationState.FINAL_CALL:
            raise ValueError("A countdown belongs only to final call")
        return self


class WagerIn(Schema):
    request_id: uuid.UUID
    side: SimulationSide
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value: object) -> object:
        if isinstance(value, str):
            return {"side_a": "team_a", "side_b": "team_b"}.get(value.lower(), value.lower())
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def exact_amount(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Send Arena Points as a decimal string")
        return value


class MatchView(Schema):
    id: uuid.UUID
    arena_id: uuid.UUID
    number: int
    team_a: str
    team_b: str
    state: SimulationState
    outcome: SimulationOutcome | None
    version: int
    final_call_ends_at: datetime | None
    simulation_fee_rate: Decimal
    team_a_pool: Decimal
    team_b_pool: Decimal
    odds_a: str | None = None
    odds_b: str | None = None
    settlement_receipt_id: uuid.UUID | None = None


class SettlementView(Schema):
    id: uuid.UUID
    match_id: uuid.UUID
    outcome: SimulationOutcome
    total_staked: Decimal
    fee_amount: Decimal
    distributed: Decimal
    refunded: bool
    created_at: datetime


class TransitionOut(Schema):
    match: MatchView
    settlement: SettlementView | None = None
    replayed: bool = False


class WagerOut(Schema):
    id: uuid.UUID
    match: MatchView
    wallet: WalletOut
    replayed: bool = False


class OwnReceipt(Schema):
    stake_id: uuid.UUID
    kind: Literal["SETTLEMENT_WIN", "SETTLEMENT_REFUND", "SETTLEMENT_LOSS"]
    amount: str
    ledger_entry_id: uuid.UUID | None = None
