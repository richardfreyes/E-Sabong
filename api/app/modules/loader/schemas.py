import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import ConfigDict, Field

from app.modules.balances.models import MAX_OPERATION_AMOUNT
from app.shared.schemas import Schema

Amount = Annotated[Decimal, Field(gt=0, le=MAX_OPERATION_AMOUNT, max_digits=9, decimal_places=2)]
Key = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Reason = Annotated[str, Field(min_length=5, max_length=300)]


class GrantIn(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_user_id: uuid.UUID
    amount: Amount
    reason: Reason
    idempotency_key: Key


class AllocationOut(Schema):
    balance: Decimal
    username: str


class AccountOut(Schema):
    id: uuid.UUID
    username: str
    full_name: str
    account_type: str
    status: str
    parent_id: uuid.UUID | None
    parent_username: str | None
    balance: Decimal


class GrantOut(Schema):
    id: uuid.UUID
    reference: str
    loader_id: uuid.UUID
    recipient_id: uuid.UUID
    recipient_username: str
    recipient_type: str
    amount: Decimal
    reason: str
    loader_balance_after: Decimal
    recipient_balance_after: Decimal
    created_at: datetime
    replayed: bool = False
