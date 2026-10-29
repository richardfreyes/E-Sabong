import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.demo_points.models import AccountKind, TransactionKind
from app.shared.schemas import Schema

POINTS_DISCLAIMER = (
    "Demonstration points have no monetary value, cannot be redeemed, exchanged or "
    "withdrawn, and are not linked to match outcomes."
)

MAX_AMOUNT = 1_000_000_000


class AllocationRequest(Schema):
    user_id: uuid.UUID
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    memo: str | None = Field(default=None, max_length=300)


class TransferRequest(Schema):
    to_user_id: uuid.UUID
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    memo: str | None = Field(default=None, max_length=300)


class AdjustmentRequest(Schema):
    user_id: uuid.UUID
    direction: Literal["credit", "debit"]
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    reason: str = Field(min_length=3, max_length=300)


class Party(Schema):
    account_id: uuid.UUID
    kind: AccountKind
    user_id: uuid.UUID | None = None
    username: str | None = None
    full_name: str | None = None


class TransactionOut(Schema):
    id: uuid.UUID
    kind: TransactionKind
    amount: int
    memo: str | None
    source: Party
    destination: Party
    created_by_id: uuid.UUID
    created_by_username: str | None = None
    created_at: datetime


class LedgerEntryOut(Schema):
    id: uuid.UUID
    transaction_id: uuid.UUID
    kind: TransactionKind
    delta: int
    balance_after: int
    memo: str | None
    counterparty: Party | None = None
    created_at: datetime


class AccountOut(Schema):
    user_id: uuid.UUID
    username: str
    full_name: str
    balance: int
    disclaimer: str = POINTS_DISCLAIMER


class PointsSummary(Schema):
    scope: Literal["tenant", "downline"]
    total_issued: int | None = None
    circulating: int
    own_balance: int | None = None
    accounts: int
    transactions_30d: dict[str, int]
    disclaimer: str = POINTS_DISCLAIMER
