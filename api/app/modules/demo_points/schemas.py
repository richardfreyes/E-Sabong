import uuid
from datetime import datetime
from typing import Literal

from app.modules.demo_points.models import AccountKind, TransactionKind, TransactionType
from app.shared.schemas import Schema

POINTS_DISCLAIMER = (
    "Legacy points archive (read-only). This module is retired: no points can be issued, transferred or refunded. "
    "Points never had monetary value and cannot be redeemed, exchanged or withdrawn."
)

MAX_AMOUNT = 1_000_000_000
MAX_BALANCE = 9_007_199_254_740_991  # exact whole points in both PostgreSQL and Angular


class Party(Schema):
    account_id: uuid.UUID
    kind: AccountKind
    user_id: uuid.UUID | None = None
    username: str | None = None
    full_name: str | None = None


TransactionStatus = Literal["completed", "reversed"]


class TransactionOut(Schema):
    id: uuid.UUID
    kind: TransactionKind
    type: TransactionType
    # Adjustments only: "credit" adds points to the account, "debit" removes them.
    direction: Literal["credit", "debit"] | None = None
    amount: int
    memo: str | None
    source: Party
    destination: Party
    created_by_id: uuid.UUID
    created_by_username: str | None = None
    created_at: datetime
    tenant_id: uuid.UUID
    reference: str
    # Ledger rows exist only after an atomic commit. "reversed": a refund has been posted for it.
    status: TransactionStatus = "completed"
    source_balance_before: int
    source_balance_after: int
    destination_balance_before: int
    destination_balance_after: int
    reverses_transaction_id: uuid.UUID | None = None
    refund_transaction_id: uuid.UUID | None = None


class LedgerEntryOut(Schema):
    id: uuid.UUID
    transaction_id: uuid.UUID
    kind: TransactionKind
    entry_type: Literal["credit", "debit"]
    delta: int
    balance_after: int
    balance_before: int
    memo: str | None
    counterparty: Party | None = None
    created_at: datetime


class AccountOut(Schema):
    user_id: uuid.UUID
    username: str
    full_name: str
    balance: int
    account_id: uuid.UUID | None = None
    disclaimer: str = POINTS_DISCLAIMER


class PointsSummary(Schema):
    scope: Literal["tenant", "downline"]
    total_issued: int | None = None
    circulating: int
    own_balance: int | None = None
    accounts: int
    transactions_30d: dict[str, int]
    gross_issued: int = 0  # every issuance in scope
    total_transferred: int = 0  # transfers and refunds out of your wallet
    total_received: int = 0  # transfers and refunds into your wallet
    total_distributed: int = 0  # points you placed in other accounts: your outgoing transfers and direct credits
    completed_transactions: int = 0
    disclaimer: str = POINTS_DISCLAIMER


class AccountMismatch(Schema):
    account_id: uuid.UUID
    kind: AccountKind
    user_id: uuid.UUID | None = None
    username: str | None = None
    balance: int
    ledger_balance: int


class LedgerIntegrity(Schema):
    """Database reconciliation: every balance equals the sum of its ledger entries, and every
    transaction has exactly two entries that net to zero."""

    consistent: bool
    accounts_checked: int
    transactions_checked: int
    ledger_net: int  # sum of every ledger delta; 0 for double entry
    balance_net: int  # sum of every stored balance, the issuance contra-account included; 0 when consistent
    mismatched_accounts: list[AccountMismatch]
    unbalanced_transactions: list[uuid.UUID]
    checked_at: datetime
