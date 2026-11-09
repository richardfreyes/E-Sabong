"""Agent and master agent commissions on official MERON/WALA results.

Commission is earnings, never wallet: nothing here posts to a play wallet or a points account.

* ``agent_commission_rates``: a master agent's row holds their total allocation; an agent's row
  holds the rate their master agent assigned. No row means the default (8.5% total, 0% agent).
* ``bets`` carries a snapshot of the agent, master agent and both rates taken when the bet was
  accepted (see ``betting.service.place_bet``), so later rate changes never touch existing bets.
* ``commission_entries`` is an append-only ledger. Each settlement of a fight posts, per bet, the
  difference between what the bet should have earned for the official result and what the ledger
  already holds: a repeat posts nothing, a correction posts a reversal.
"""

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum

RATE = Numeric(6, 4)
POINTS = Numeric(16, 2)


class CommissionStatus(StrEnum):
    EARNED = "earned"  # the ledger moved up: a MERON/WALA result was declared (or restored)
    REVERSED = "reversed"  # the ledger moved down: the result was corrected to DRAW or cancelled


class AgentCommissionRate(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "agent_commission_rates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_agent_commission_rates_user"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_agent_commission_rates_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_agent_commission_rates_updated_by",
        ),
        CheckConstraint("rate >= 0 AND rate <= 1", name="rate_range"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    updated_by_id: Mapped[uuid.UUID | None]


class CommissionEntry(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "commission_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "bet_id", "seq", name="uq_commission_entries_bet_seq"),
        ForeignKeyConstraint(["tenant_id", "bet_id"], ["bets.tenant_id", "bets.id"], name="fk_commission_entries_bet"),
        ForeignKeyConstraint(["tenant_id", "match_id"], ["matches.tenant_id", "matches.id"], name="fk_commission_entries_match"),
        ForeignKeyConstraint(
            ["tenant_id", "player_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_commission_entries_player"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_commission_entries_agent"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "master_agent_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_commission_entries_master_agent",
        ),
        CheckConstraint("bet_amount > 0", name="bet_amount_positive"),
        CheckConstraint("agent_rate >= 0 AND master_rate >= 0", name="rates_non_negative"),
        Index("ix_commission_entries_agent", "tenant_id", "agent_id", "created_at"),
        Index("ix_commission_entries_master", "tenant_id", "master_agent_id", "created_at"),
        Index("ix_commission_entries_match", "tenant_id", "match_id"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    bet_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_id: Mapped[uuid.UUID | None]
    master_agent_id: Mapped[uuid.UUID | None]
    bet_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    agent_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    master_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    # Signed: positive when earned, negative on a reversal.
    agent_points: Mapped[Decimal] = mapped_column(POINTS, nullable=False)
    master_points: Mapped[Decimal] = mapped_column(POINTS, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    result_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[CommissionStatus] = mapped_column(str_enum(CommissionStatus), nullable=False)
