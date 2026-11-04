"""Neutral tournament schema; the existing play_wallets ledger remains the sole balance."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class SimulationState(StrEnum):
    PENDING = "pending"
    WAGERING_OPEN = "wagering_open"
    FINAL_CALL = "final_call"
    WAGERING_CLOSED = "wagering_closed"
    SIMULATION_RUNNING = "simulation_running"
    RESULT_DECLARED = "result_declared"
    SETTLED = "settled"


class SimulationOutcome(StrEnum):
    TEAM_A = "team_a"
    TEAM_B = "team_b"
    DRAW = "draw"
    CANCELLED = "cancelled"


class SimulationSide(StrEnum):
    TEAM_A = "team_a"
    TEAM_B = "team_b"


class SimulationArena(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "simulation_arenas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_simulation_arenas_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_simulation_arenas_name"),
        CheckConstraint("length(trim(name)) > 0", name="name_nonempty"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Each arena is explicitly enabled by its tenant administrator.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class SimulationController(Base, CreatedAt, TenantScoped):
    __tablename__ = "simulation_controllers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "arena_id"],
            ["simulation_arenas.tenant_id", "simulation_arenas.id"],
            name="fk_simulation_controllers_arena",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_simulation_controllers_user",
            ondelete="RESTRICT",
        ),
    )

    arena_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class SimulationMatch(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "simulation_matches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_simulation_matches_tenant_id_id"),
        UniqueConstraint("tenant_id", "arena_id", "number", name="uq_simulation_matches_number"),
        Index(
            "uq_simulation_matches_active",
            "tenant_id",
            "arena_id",
            unique=True,
            postgresql_where=text("state NOT IN ('pending', 'settled')"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "arena_id"],
            ["simulation_arenas.tenant_id", "simulation_arenas.id"],
            name="fk_simulation_matches_arena",
            ondelete="RESTRICT",
        ),
        CheckConstraint("number > 0 AND version >= 0", name="number_version"),
        CheckConstraint("team_a_pool >= 0 AND team_b_pool >= 0", name="pools_non_negative"),
        CheckConstraint("simulation_fee_rate >= 0 AND simulation_fee_rate < 0.5", name="fee_range"),
        CheckConstraint("length(trim(team_a)) > 0 AND length(trim(team_b)) > 0", name="teams_nonempty"),
        CheckConstraint(
            "(state IN ('result_declared', 'settled')) = (outcome IS NOT NULL)",
            name="result_matches_state",
        ),
    )

    arena_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    team_a: Mapped[str] = mapped_column(String(120), nullable=False)
    team_b: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[SimulationState] = mapped_column(str_enum(SimulationState), nullable=False, default=SimulationState.PENDING)
    outcome: Mapped[SimulationOutcome | None] = mapped_column(str_enum(SimulationOutcome))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_call_ends_at: Mapped[datetime | None]
    simulation_fee_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.05"))
    team_a_pool: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0.00"))
    team_b_pool: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0.00"))


class SimulationStake(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "simulation_stakes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_simulation_stakes_tenant_id_id"),
        UniqueConstraint("tenant_id", "player_id", "request_id", name="uq_simulation_stakes_request"),
        Index("ix_simulation_stakes_match", "tenant_id", "match_id"),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["simulation_matches.tenant_id", "simulation_matches.id"],
            name="fk_simulation_stakes_match",
            ondelete="RESTRICT",
        ),
        # Reference the player's ONE existing Arena Points wallet, never a second balance.
        ForeignKeyConstraint(
            ["tenant_id", "player_id"],
            ["play_wallets.tenant_id", "play_wallets.user_id"],
            name="fk_simulation_stakes_wallet",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0 AND amount <> 'NaN'::numeric", name="amount_positive"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    side: Mapped[SimulationSide] = mapped_column(str_enum(SimulationSide), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class SimulationCommand(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    """Immutable command receipts reserve a per-actor idempotency key for the controller phase."""

    __tablename__ = "simulation_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "actor_id", "request_id", name="uq_simulation_commands_request"),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["simulation_matches.tenant_id", "simulation_matches.id"],
            name="fk_simulation_commands_match",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_simulation_commands_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
        CheckConstraint("version >= 0", name="version_non_negative"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(nullable=False)


class SimulationSettlement(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    """One immutable receipt per match; payout lines remain private to their participants."""

    __tablename__ = "simulation_settlements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "match_id", name="uq_simulation_settlements_match"),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["simulation_matches.tenant_id", "simulation_matches.id"],
            name="fk_simulation_settlements_match",
            ondelete="RESTRICT",
        ),
        CheckConstraint("total_staked >= 0 AND fee_amount >= 0 AND distributed >= 0", name="amounts_non_negative"),
        CheckConstraint("total_staked = fee_amount + distributed", name="conservation"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    outcome: Mapped[SimulationOutcome] = mapped_column(str_enum(SimulationOutcome), nullable=False)
    total_staked: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    distributed: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    refunded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lines: Mapped[dict[str, Any]] = mapped_column(nullable=False)
