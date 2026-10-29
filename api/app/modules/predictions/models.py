"""Players' non-monetary fight predictions. A prediction records a side only: it carries no
amount, and its outcome never moves demonstration points."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class PredictionSide(StrEnum):
    MERON = "meron"
    WALA = "wala"
    DRAW = "draw"


class PredictionStatus(StrEnum):
    PENDING = "pending"
    WIN = "win"
    LOSE = "lose"
    VOID = "void"


class Prediction(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "predictions"
    __table_args__ = (
        # One prediction per player per fight; changing sides updates this row while the window is open.
        UniqueConstraint("tenant_id", "user_id", "match_id", name="uq_predictions_player_fight"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_predictions_player",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"], ["matches.tenant_id", "matches.id"], name="fk_predictions_match", ondelete="RESTRICT"
        ),
        Index("ix_predictions_match", "tenant_id", "match_id"),
        Index("ix_predictions_player_created", "tenant_id", "user_id", "created_at"),
        CheckConstraint("changes >= 0", name="changes_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    side: Mapped[PredictionSide] = mapped_column(str_enum(PredictionSide), nullable=False)
    status: Mapped[PredictionStatus] = mapped_column(str_enum(PredictionStatus), nullable=False, default=PredictionStatus.PENDING)
    changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decided_at: Mapped[datetime | None]
