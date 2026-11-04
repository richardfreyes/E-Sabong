"""Administrator balance management on the existing play-balance ledger.

Adds ``balance_operations`` (uniform grants, uniform resets and maker-checker corrections),
links each resulting ``play_wallet_entries`` row to its operation, and adds a reset policy to
``betting_settings``. No second balance is introduced and no existing row is changed.

Database guards:

* operations cannot be deleted; once applied or rejected they cannot change at all, and a
  pending correction can only be decided by a different account than the one that proposed it;
* an applied operation must have exactly ``accounts_affected`` ledger entries summing to
  ``total_delta`` by commit time (deferred), so a balance change never lacks its ledger rows;
* only operation-backed entries may use the new entry kinds.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

OLD_KINDS = "'grant', 'stake', 'payout', 'refund', 'resettlement'"
NEW_KINDS = OLD_KINDS + ", 'uniform_grant', 'reset', 'correction'"


def upgrade() -> None:
    op.execute("""
CREATE TABLE balance_operations (
    reference VARCHAR(32) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    direction VARCHAR(32),
    target_user_id UUID,
    corrects_entry_id UUID,
    corrects_operation_id UUID,
    remarks VARCHAR(300),
    created_by_id UUID NOT NULL,
    reviewed_by_id UUID,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    review_remarks VARCHAR(300),
    accounts_affected INTEGER DEFAULT 0 NOT NULL,
    total_delta NUMERIC(16, 2) DEFAULT 0 NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_balance_operations PRIMARY KEY (id),
    CONSTRAINT uq_balance_operations_tenant_id_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_balance_operations_reference UNIQUE (tenant_id, reference),
    CONSTRAINT uq_balance_operations_key UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_balance_operations_created_by FOREIGN KEY(tenant_id, created_by_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_balance_operations_reviewed_by FOREIGN KEY(tenant_id, reviewed_by_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_balance_operations_target FOREIGN KEY(tenant_id, target_user_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_balance_operations_corrects_entry FOREIGN KEY(corrects_entry_id)
        REFERENCES play_wallet_entries (id) ON DELETE RESTRICT,
    CONSTRAINT fk_balance_operations_corrects_operation FOREIGN KEY(tenant_id, corrects_operation_id)
        REFERENCES balance_operations (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_balance_operations_balanceoperationkind CHECK (kind IN ('uniform_grant', 'reset', 'correction')),
    CONSTRAINT ck_balance_operations_balanceoperationstatus CHECK (status IN ('pending', 'applied', 'rejected')),
    CONSTRAINT ck_balance_operations_correctiondirection CHECK (direction IN ('credit', 'debit')),
    CONSTRAINT ck_balance_operations_amount_range CHECK (amount >= 0 AND amount <= 1000000),
    CONSTRAINT ck_balance_operations_shape CHECK (
        (kind = 'uniform_grant' AND status = 'applied' AND amount > 0 AND direction IS NULL
            AND target_user_id IS NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
        OR (kind = 'reset' AND status = 'applied' AND direction IS NULL
            AND target_user_id IS NULL AND corrects_entry_id IS NULL AND corrects_operation_id IS NULL)
        OR (kind = 'correction' AND amount > 0 AND direction IS NOT NULL AND target_user_id IS NOT NULL
            AND remarks IS NOT NULL
            AND (corrects_entry_id IS NULL) <> (corrects_operation_id IS NULL))
    ),
    CONSTRAINT ck_balance_operations_review CHECK (
        (status = 'pending' AND reviewed_by_id IS NULL AND reviewed_at IS NULL)
        OR (kind <> 'correction' AND status = 'applied')
        OR (kind = 'correction' AND status <> 'pending' AND reviewed_by_id IS NOT NULL
            AND reviewed_at IS NOT NULL AND reviewed_by_id <> created_by_id)
    ),
    CONSTRAINT fk_balance_operations_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_balance_operations_tenant_id ON balance_operations (tenant_id)")
    op.execute("CREATE INDEX ix_balance_operations_history ON balance_operations (tenant_id, created_at, id)")
    op.execute("CREATE INDEX ix_balance_operations_corrects_entry ON balance_operations (corrects_entry_id)")
    op.execute(
        "CREATE INDEX ix_balance_operations_corrects_operation ON balance_operations (corrects_operation_id, target_user_id)"
    )
    enable_tenant_rls("balance_operations")
    grant("balance_operations", app="SELECT, INSERT, UPDATE", platform="SELECT")

    # Entries produced by an operation point back to it; the new kinds require that link.
    op.execute("ALTER TABLE play_wallet_entries ADD COLUMN operation_id UUID")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT fk_play_wallet_entries_operation "
        "FOREIGN KEY (tenant_id, operation_id) REFERENCES balance_operations (tenant_id, id) ON DELETE RESTRICT"
    )
    op.execute("CREATE INDEX ix_play_wallet_entries_operation ON play_wallet_entries (operation_id)")
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        f"ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind CHECK (kind IN ({NEW_KINDS}))"
    )
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_operation_kind CHECK ("
        "(kind IN ('uniform_grant', 'reset', 'correction')) = (operation_id IS NOT NULL))"
    )

    op.execute(
        "ALTER TABLE betting_settings ADD COLUMN reset_policy VARCHAR(32) DEFAULT 'disabled' NOT NULL "
        "CONSTRAINT ck_betting_settings_resetpolicy CHECK (reset_policy IN ('disabled', 'starting_balance'))"
    )

    op.execute("""
        CREATE FUNCTION balance_operation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'balance_operations rows are permanent' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.status <> 'pending' THEN
                RAISE EXCEPTION 'a decided balance operation is immutable' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF (NEW.reference, NEW.kind, NEW.amount, NEW.direction, NEW.target_user_id, NEW.corrects_entry_id,
                NEW.corrects_operation_id, NEW.remarks, NEW.created_by_id, NEW.idempotency_key, NEW.request_hash,
                NEW.created_at, NEW.tenant_id)
               IS DISTINCT FROM
               (OLD.reference, OLD.kind, OLD.amount, OLD.direction, OLD.target_user_id, OLD.corrects_entry_id,
                OLD.corrects_operation_id, OLD.remarks, OLD.created_by_id, OLD.idempotency_key, OLD.request_hash,
                OLD.created_at, OLD.tenant_id) THEN
                RAISE EXCEPTION 'only the review of a pending correction may change' USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER balance_operations_guard BEFORE UPDATE OR DELETE ON balance_operations "
        "FOR EACH ROW EXECUTE FUNCTION balance_operation_guard()"
    )
    op.execute("""
        CREATE FUNCTION balance_operation_posted() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE n integer; total numeric;
        BEGIN
            IF NEW.status = 'applied' THEN
                SELECT count(*), coalesce(sum(delta), 0) INTO n, total FROM play_wallet_entries
                WHERE tenant_id = NEW.tenant_id AND operation_id = NEW.id;
                IF n <> NEW.accounts_affected OR total <> NEW.total_delta THEN
                    RAISE EXCEPTION 'applied balance operation % does not match its ledger entries', NEW.reference
                        USING ERRCODE = 'check_violation';
                END IF;
            ELSIF EXISTS (SELECT 1 FROM play_wallet_entries WHERE tenant_id = NEW.tenant_id AND operation_id = NEW.id) THEN
                RAISE EXCEPTION 'balance operation % has entries but is not applied', NEW.reference
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER balance_operations_posted AFTER INSERT OR UPDATE ON balance_operations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION balance_operation_posted()"
    )

    # Existing tenant administrator system roles receive the new permission (FORCE RLS would hide
    # every row from the migration owner, so it is lifted for this additive backfill only).
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, 'balance.manage' FROM roles WHERE name = 'tenant_admin' AND is_system
        ON CONFLICT DO NOTHING
    """)
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Refuse to discard balance-management history; an exception rolls back the whole downgrade.
    op.execute("ALTER TABLE balance_operations NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM balance_operations) THEN
                RAISE EXCEPTION 'balance_operations has history; refusing to downgrade';
            END IF;
        END $$
    """)
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM role_permissions WHERE permission = 'balance.manage'")
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE betting_settings DROP COLUMN reset_policy")
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_operation_kind")
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        f"ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind CHECK (kind IN ({OLD_KINDS}))"
    )
    op.execute("DROP INDEX ix_play_wallet_entries_operation")
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT fk_play_wallet_entries_operation")
    op.execute("ALTER TABLE play_wallet_entries DROP COLUMN operation_id")
    op.execute("DROP TRIGGER balance_operations_posted ON balance_operations")
    op.execute("DROP TRIGGER balance_operations_guard ON balance_operations")
    op.execute("DROP FUNCTION balance_operation_posted()")
    op.execute("DROP FUNCTION balance_operation_guard()")
    op.drop_table("balance_operations")
