import uuid
from datetime import datetime
from typing import Annotated

from pydantic import ConfigDict, Field

from app.modules.resources.models import MAX_AMOUNT, ResourceAction, ResourceKind
from app.shared.schemas import Schema

Amount = Annotated[int, Field(strict=True, ge=1, le=MAX_AMOUNT)]


class ResourceRequest(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    idempotency_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=300)


class UsageRequest(ResourceRequest):
    resource_kind: ResourceKind
    amount: Amount


class IssuanceRequest(UsageRequest):
    """Direct admin provisioning into one account. Nothing is debited from any other account."""

    target_user_id: uuid.UUID


class RefundRequest(ResourceRequest):
    transaction_id: uuid.UUID


class SettingsRequest(Schema):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class BalanceOut(Schema):
    user_id: uuid.UUID
    resource_kind: ResourceKind
    balance: int
    available_balance: int
    disclaimer: str = "Operational resource units only. Non-monetary; no cash value."


class TargetOut(Schema):
    id: uuid.UUID
    username: str
    full_name: str
    account_type: str


class ResourceReceipt(Schema):
    id: uuid.UUID
    kind: ResourceAction
    resource_kind: ResourceKind
    amount: int
    source_user_id: uuid.UUID | None
    target_user_id: uuid.UUID | None
    source_balance_after: int | None
    target_balance_after: int | None
    created_by_id: uuid.UUID
    created_at: datetime
    reason: str
    refund_of_id: uuid.UUID | None
    replayed: bool = False
