"""Tables created by migrations 0024 and 0025, kept mapped so the schema and models agree.

``balance_transfers`` records an administrator moving play balance from their own wallet to a
master agent or agent (``player_id`` is that recipient). Agents do not move balances to players,
and players do not file inbox requests.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum

MONEY = Numeric(14, 2)


class BalanceTransfer(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "balance_transfers"

    reference: Mapped[str] = mapped_column(nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    sender_balance_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    sender_balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    player_balance_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    player_balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="applied")
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(nullable=False)


class InboxRequest(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "inbox_requests"

    player_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    upline_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending")
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(nullable=False)


class InvitationRole(StrEnum):
    AGENT = "agent"
    SUB_AGENT = "sub_agent"
    PLAYER = "player"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class AccountInvitation(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    """One invitation, owned only by the account that created it.

    ``invited_by_user_id`` is that creator. Parentage, downline membership, and tenant
    administration do not grant access to the row.
    """

    __tablename__ = "account_invitations"

    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    invitee_user_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    invitee_username: Mapped[str | None] = mapped_column(String(64), default=None)
    invitee_full_name: Mapped[str | None] = mapped_column(String(200), default=None)
    account_type: Mapped[InvitationRole] = mapped_column(str_enum(InvitationRole, name="invitationrole"), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        str_enum(InvitationStatus, name="invitationstatus"), nullable=False, default=InvitationStatus.PENDING
    )
