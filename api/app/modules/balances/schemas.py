import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import ConfigDict, Field, model_validator

from app.modules.balances.models import (
    MAX_OPERATION_AMOUNT,
    BalanceOperationKind,
    BalanceOperationStatus,
    CorrectionDirection,
)
from app.modules.betting.schemas import DISCLAIMER, StatementSummary
from app.shared.schemas import Schema

Amount = Annotated[Decimal, Field(gt=0, le=MAX_OPERATION_AMOUNT, max_digits=9, decimal_places=2)]
Key = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Remarks = Annotated[str, Field(min_length=1, max_length=300)]
Reason = Annotated[str, Field(min_length=5, max_length=300)]


class Request(Schema):
    # Unknown fields are refused: a sender, tenant or balance can never be supplied by a client.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)
    idempotency_key: Key


class UniformGrantIn(Request):
    """The same amount to every active participant (player) of the tenant. There is no recipient."""

    amount: Amount
    remarks: Remarks | None = None


class AccountGrantIn(Request):
    """Add this amount to one active player. Other accounts are unchanged, and none is the source."""

    target_user_id: uuid.UUID
    amount: Amount
    remarks: Remarks | None = None


class ResetIn(Request):
    """Every active participant back to the configured starting balance (needs the reset policy)."""

    remarks: Remarks | None = None


class CorrectionIn(Request):
    """Correct one participant's administrator posting. Exactly one of ``entry_id`` (reverse an
    erroneous grant/reset entry, fully or partly) or ``operation_id`` (apply a uniform grant the
    participant was eligible for but missed) is required. Posts only after a second admin approves."""

    target_user_id: uuid.UUID
    entry_id: uuid.UUID | None = None
    operation_id: uuid.UUID | None = None
    amount: Amount | None = Field(default=None, description="Reversal only; defaults to the full remaining amount.")
    remarks: Reason

    @model_validator(mode="after")
    def _one_basis(self) -> "CorrectionIn":
        if (self.entry_id is None) == (self.operation_id is None):
            raise ValueError("Provide exactly one of entry_id or operation_id")
        if self.operation_id is not None and self.amount is not None:
            raise ValueError("A missed uniform grant is applied at its original amount")
        return self


class ReviewIn(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    remarks: Remarks | None = None


class RejectIn(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    remarks: Reason


class OperationOut(Schema):
    id: uuid.UUID
    reference: str
    kind: BalanceOperationKind
    status: BalanceOperationStatus
    amount: Decimal
    direction: CorrectionDirection | None
    target_user_id: uuid.UUID | None
    target_username: str | None = None
    corrects_entry_id: uuid.UUID | None
    corrects_operation_id: uuid.UUID | None
    remarks: str | None
    created_by_id: uuid.UUID
    created_by_username: str | None = None
    reviewed_by_id: uuid.UUID | None
    reviewed_by_username: str | None = None
    reviewed_at: datetime | None
    review_remarks: str | None
    accounts_affected: int
    total_delta: Decimal
    created_at: datetime
    replayed: bool = False
    disclaimer: str = DISCLAIMER


class AccountRow(Schema):
    id: uuid.UUID
    username: str
    full_name: str
    account_type: str
    status: str
    parent_id: uuid.UUID | None
    parent_username: str | None
    balance: Decimal
    has_wallet: bool
    updated_at: datetime | None


class AccountDetail(Schema):
    account: AccountRow
    statement: StatementSummary
    # Master agents and agents only. None for every other account type.
    available_earnings: Decimal | None = None
    disclaimer: str = DISCLAIMER


class BalanceSummary(Schema):
    """Totals over the accounts the caller may see."""

    accounts: int
    participants: int
    total_balance: Decimal
    pending_corrections: int | None  # administrators only
    reset_policy: str
    starting_balance: Decimal
    can_manage: bool
    # The acting administrator's own play wallet. None for every other caller.
    own_balance: Decimal | None = None
    disclaimer: str = DISCLAIMER


class WalletTransferIn(Request):
    """Move play balance from the acting administrator's wallet to one master agent or agent."""

    target_user_id: uuid.UUID
    amount: Amount
    remarks: Remarks | None = None


class WalletTransferOut(Schema):
    id: uuid.UUID
    reference: str
    target_user_id: uuid.UUID
    target_username: str
    amount: Decimal
    sender_balance_after: Decimal
    target_balance_after: Decimal
    replayed: bool = False
    disclaimer: str = DISCLAIMER


class WalletRecallIn(Request):
    """Take play balance from one master agent or agent and return it to the acting administrator."""

    target_user_id: uuid.UUID
    amount: Amount
    remarks: Reason


class WalletRecallOut(Schema):
    id: uuid.UUID
    reference: str
    target_user_id: uuid.UUID
    target_username: str
    amount: Decimal
    account_balance_after: Decimal
    admin_balance_after: Decimal
    replayed: bool = False
    disclaimer: str = DISCLAIMER


class UplineReturnRow(Schema):
    """One posted cash-out: the selected account down, the acting administrator up."""

    id: uuid.UUID
    created_at: datetime
    reference: str
    account_id: uuid.UUID
    account_username: str
    admin_id: uuid.UUID
    admin_username: str
    amount: Decimal
    account_balance_after: Decimal
    admin_balance_after: Decimal
    remarks: str | None


class EarningsToWalletIn(Request):
    """Move a master agent's or agent's available commission into that same account's wallet."""

    target_user_id: uuid.UUID
    amount: Amount
    remarks: Reason


class EarningsToWalletOut(Schema):
    id: uuid.UUID
    reference: str
    target_user_id: uuid.UUID
    target_username: str
    amount: Decimal
    earnings_before: Decimal
    earnings_after: Decimal
    wallet_after: Decimal
    replayed: bool = False
    disclaimer: str = DISCLAIMER
