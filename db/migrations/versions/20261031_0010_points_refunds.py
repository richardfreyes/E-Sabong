"""Append-only point transfer refunds; preserves every existing balance and ledger row.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable metadata only: no balance updates, deletions, backfills, or fixture data.
    op.add_column(
        "point_transactions",
        sa.Column("reverses_transaction_id", sa.UUID(), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_point_transactions_transactionkind"),
        "point_transactions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_point_transactions_transactionkind"),
        "point_transactions",
        "kind IN ('allocation', 'transfer', 'adjustment', 'refund')",
    )
    op.create_foreign_key(
        "fk_point_transactions_reversal_same_tenant",
        "point_transactions",
        "point_transactions",
        ["tenant_id", "reverses_transaction_id"],
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_point_transactions_reversal",
        "point_transactions",
        ["tenant_id", "reverses_transaction_id"],
    )


def downgrade() -> None:
    # Refuse to erase refund links or make existing refund records unreadable.
    connection = op.get_bind()
    # The migration role is subject to FORCE ROW LEVEL SECURITY and would see no tenant rows.
    # As table owner it may lift FORCE for this transaction; a failed check rolls this back too.
    op.execute("ALTER TABLE point_transactions NO FORCE ROW LEVEL SECURITY")
    if connection.execute(
        sa.text("SELECT 1 FROM point_transactions WHERE kind = 'refund' LIMIT 1")
    ).first():
        raise RuntimeError(
            "Cannot downgrade after refunds have been posted; preserve the immutable ledger"
        )
    op.execute("ALTER TABLE point_transactions FORCE ROW LEVEL SECURITY")
    op.drop_constraint(
        "uq_point_transactions_reversal", "point_transactions", type_="unique"
    )
    op.drop_constraint(
        "fk_point_transactions_reversal_same_tenant",
        "point_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_point_transactions_transactionkind"),
        "point_transactions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_point_transactions_transactionkind"),
        "point_transactions",
        "kind IN ('allocation', 'transfer', 'adjustment')",
    )
    op.drop_column("point_transactions", "reverses_transaction_id")
