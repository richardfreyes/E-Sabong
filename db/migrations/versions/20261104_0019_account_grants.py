"""Allow an administrator to add balance to one player account.

Extends the balance-operation and ledger kind checks. An account grant credits a single active
player; it does not move balance from any other account. Existing rows stay valid.

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

ENTRY_KINDS = "'grant', 'stake', 'payout', 'refund', 'resettlement', 'uniform_grant', 'reset', 'correction', 'account_grant'"
OPERATION_KINDS = "'uniform_grant', 'reset', 'correction', 'account_grant'"
ADMIN_ENTRY_KINDS = "'uniform_grant', 'reset', 'correction', 'account_grant'"


def upgrade() -> None:
    op.execute("ALTER TABLE balance_operations DROP CONSTRAINT ck_balance_operations_balanceoperationkind")
    op.execute(
        "ALTER TABLE balance_operations ADD CONSTRAINT ck_balance_operations_balanceoperationkind "
        f"CHECK (kind IN ({OPERATION_KINDS}))"
    )
    op.execute("ALTER TABLE balance_operations DROP CONSTRAINT ck_balance_operations_shape")
    op.execute(
        """
        ALTER TABLE balance_operations ADD CONSTRAINT ck_balance_operations_shape CHECK (
            (kind = 'uniform_grant' AND status = 'applied' AND amount > 0 AND direction IS NULL
                AND target_user_id IS NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
            OR (kind = 'account_grant' AND status = 'applied' AND amount > 0 AND direction IS NULL
                AND target_user_id IS NOT NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
            OR (kind = 'reset' AND status = 'applied' AND direction IS NULL
                AND target_user_id IS NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
            OR (kind = 'correction' AND amount > 0 AND direction IS NOT NULL AND target_user_id IS NOT NULL
                AND remarks IS NOT NULL
                AND (corrects_entry_id IS NULL) <> (corrects_operation_id IS NULL))
        )
        """
    )
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        f"ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind CHECK (kind IN ({ENTRY_KINDS}))"
    )
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_operation_kind")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_operation_kind CHECK ("
        f"(kind IN ({ADMIN_ENTRY_KINDS})) = (operation_id IS NOT NULL))"
    )


def downgrade() -> None:
    for table in ("balance_operations", "play_wallet_entries"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM balance_operations WHERE kind = 'account_grant')
               OR EXISTS (SELECT 1 FROM play_wallet_entries WHERE kind = 'account_grant') THEN
                RAISE EXCEPTION 'account grants exist; refusing to downgrade';
            END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE balance_operations DROP CONSTRAINT ck_balance_operations_balanceoperationkind")
    op.execute(
        "ALTER TABLE balance_operations ADD CONSTRAINT ck_balance_operations_balanceoperationkind "
        "CHECK (kind IN ('uniform_grant', 'reset', 'correction'))"
    )
    op.execute("ALTER TABLE balance_operations DROP CONSTRAINT ck_balance_operations_shape")
    op.execute(
        """
        ALTER TABLE balance_operations ADD CONSTRAINT ck_balance_operations_shape CHECK (
            (kind = 'uniform_grant' AND status = 'applied' AND amount > 0 AND direction IS NULL
                AND target_user_id IS NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
            OR (kind = 'reset' AND status = 'applied' AND direction IS NULL
                AND target_user_id IS NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
            OR (kind = 'correction' AND amount > 0 AND direction IS NOT NULL AND target_user_id IS NOT NULL
                AND remarks IS NOT NULL
                AND (corrects_entry_id IS NULL) <> (corrects_operation_id IS NULL))
        )
        """
    )
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind "
        "CHECK (kind IN ('grant', 'stake', 'payout', 'refund', 'resettlement', 'uniform_grant', 'reset', 'correction'))"
    )
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_operation_kind")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_operation_kind CHECK ("
        "(kind IN ('uniform_grant', 'reset', 'correction')) = (operation_id IS NOT NULL))"
    )
    for table in ("balance_operations", "play_wallet_entries"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
