"""Database guards for the points ledger. Adds triggers only: no rows are read, changed or deleted.

* ``point_transactions`` and ``point_ledger_entries`` reject UPDATE and DELETE for every role,
  including the table owner (grants already restrict the application roles).
* New ``point_accounts`` must open at a zero balance; their identity columns never change and
  accounts are never deleted.
* Any balance change must be matched, before commit, by a ledger entry for that account written
  in the same transaction whose ``balance_after`` equals the committed balance. A direct
  ``UPDATE point_accounts SET balance = ...`` outside the wallet service therefore fails.

Existing balances and ledger rows are untouched; the guards apply to future writes only.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION points_ledger_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$
        """
    )
    for table in ("point_transactions", "point_ledger_entries"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION points_ledger_immutable()"
        )
    op.execute(
        "CREATE TRIGGER point_accounts_no_delete BEFORE DELETE ON point_accounts "
        "FOR EACH ROW EXECUTE FUNCTION points_ledger_immutable()"
    )
    op.execute(
        """
        CREATE FUNCTION point_accounts_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.balance <> 0 THEN
                    RAISE EXCEPTION 'point accounts must open with a zero balance'
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.kind <> OLD.kind
                  OR NEW.user_id IS DISTINCT FROM OLD.user_id THEN
                RAISE EXCEPTION 'point account identity cannot change' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER point_accounts_guard BEFORE INSERT OR UPDATE ON point_accounts "
        "FOR EACH ROW EXECUTE FUNCTION point_accounts_guard()"
    )
    # Ledger entries take created_at = now(), the transaction start time, so "written in this
    # transaction" is an indexed lookup on (account_id, created_at).
    op.execute(
        """
        CREATE FUNCTION point_accounts_balance_posted() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            committed bigint;
        BEGIN
            SELECT balance INTO committed FROM point_accounts WHERE id = NEW.id;
            IF NOT EXISTS (
                SELECT 1 FROM point_ledger_entries
                WHERE account_id = NEW.id AND created_at = now() AND balance_after = committed
            ) THEN
                RAISE EXCEPTION 'point account % balance changed without a matching ledger entry', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER point_accounts_balance_posted AFTER UPDATE OF balance ON point_accounts "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (OLD.balance IS DISTINCT FROM NEW.balance) "
        "EXECUTE FUNCTION point_accounts_balance_posted()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS point_accounts_balance_posted ON point_accounts")
    op.execute("DROP TRIGGER IF EXISTS point_accounts_guard ON point_accounts")
    op.execute("DROP TRIGGER IF EXISTS point_accounts_no_delete ON point_accounts")
    for table in ("point_transactions", "point_ledger_entries"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS point_accounts_balance_posted()")
    op.execute("DROP FUNCTION IF EXISTS point_accounts_guard()")
    op.execute("DROP FUNCTION IF EXISTS points_ledger_immutable()")
