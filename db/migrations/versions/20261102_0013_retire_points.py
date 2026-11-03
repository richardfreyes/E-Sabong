"""Retire the transferable points module: the ledger becomes a read-only archive

Adds BEFORE INSERT/UPDATE/DELETE triggers on ``point_accounts``, ``point_transactions`` and
``point_ledger_entries`` that reject every row mutation, for every role. Together with migration 0011
(ledger rows immutable; balances change only with a same-transaction ledger entry; accounts never
deleted) this makes the archive read-only: no allocation, transfer, adjustment or refund can be
recorded, and no balance can change.

Nothing is read, changed, moved or deleted. Existing balances are not added to Arena Points (the
play balance) or to anything else. Before/after evidence: ``python -m app.cli points-archive-report``.

Downgrade removes the triggers only (the application code that wrote points is also gone, so
re-enabling writes needs the previous release as well).

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("point_accounts", "point_transactions", "point_ledger_entries")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION points_retired() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'The points module is retired: % is a read-only archive', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
        END $$;
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_retired BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION points_retired()"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_retired ON {table}")
    op.execute("DROP FUNCTION IF EXISTS points_retired()")
