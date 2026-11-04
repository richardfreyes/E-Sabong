"""Isolated non-monetary operational resource ledger; existing balances are untouched.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE resource_settings (
    enabled BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_resource_settings PRIMARY KEY (tenant_id),
    CONSTRAINT fk_resource_settings_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("""
CREATE INDEX ix_resource_settings_tenant_id ON resource_settings (tenant_id)
""")
    enable_tenant_rls("resource_settings")
    grant("resource_settings", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE resource_wallets (
    user_id UUID NOT NULL,
    resource_kind VARCHAR(32) NOT NULL,
    balance BIGINT DEFAULT 0 NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_resource_wallets PRIMARY KEY (id),
    CONSTRAINT uq_resource_wallet_owner_kind UNIQUE (tenant_id, user_id, resource_kind),
    CONSTRAINT uq_resource_wallet_identity UNIQUE (tenant_id, id, resource_kind),
    CONSTRAINT fk_resource_wallets_tenant_id_tenant_users FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT ck_resource_wallets_resource_balance_range CHECK (balance BETWEEN 0 AND 9007199254740991),
    CONSTRAINT ck_resource_wallets_resourcekind CHECK (resource_kind IN ('compute', 'admin_utilities', 'seats')),
    CONSTRAINT fk_resource_wallets_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("""
CREATE INDEX ix_resource_wallets_tenant_id ON resource_wallets (tenant_id)
""")
    enable_tenant_rls("resource_wallets")
    grant("resource_wallets", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE resource_transactions (
    kind VARCHAR(32) NOT NULL,
    resource_kind VARCHAR(32) NOT NULL,
    amount BIGINT NOT NULL,
    source_wallet_id UUID,
    target_wallet_id UUID,
    created_by_id UUID NOT NULL,
    refund_of_id UUID,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    reason VARCHAR(300) NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_resource_transactions PRIMARY KEY (id),
    CONSTRAINT uq_resource_transaction_key UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT uq_resource_transaction_identity UNIQUE (tenant_id, id, resource_kind),
    CONSTRAINT uq_resource_usage_refund UNIQUE (tenant_id, refund_of_id),
    CONSTRAINT fk_resource_transaction_source FOREIGN KEY(tenant_id, source_wallet_id, resource_kind) REFERENCES
        resource_wallets (tenant_id, id, resource_kind),
    CONSTRAINT fk_resource_transaction_target FOREIGN KEY(tenant_id, target_wallet_id, resource_kind) REFERENCES
        resource_wallets (tenant_id, id, resource_kind),
    CONSTRAINT fk_resource_transactions_tenant_id_tenant_users FOREIGN KEY(tenant_id, created_by_id) REFERENCES tenant_users
        (tenant_id, id),
    CONSTRAINT fk_resource_transactions_tenant_id_resource_transactions FOREIGN KEY(tenant_id, refund_of_id, resource_kind)
        REFERENCES resource_transactions (tenant_id, id, resource_kind),
    CONSTRAINT ck_resource_transactions_resource_amount_range CHECK (amount BETWEEN 1 AND 1000000000),
    CONSTRAINT ck_resource_transactions_resource_action_parties CHECK ((kind = 'issue' AND source_wallet_id IS NULL AND
        target_wallet_id IS NOT NULL AND refund_of_id IS NULL) OR (kind = 'consume' AND source_wallet_id IS NOT NULL AND
        target_wallet_id IS NULL AND refund_of_id IS NULL) OR (kind = 'refund' AND source_wallet_id IS NULL AND
        target_wallet_id IS NOT NULL AND refund_of_id IS NOT NULL)),
    -- No transaction may debit one account and credit another: user-to-user movement is impossible.
    CONSTRAINT ck_resource_transactions_no_transfer CHECK (source_wallet_id IS NULL OR target_wallet_id IS NULL),
    CONSTRAINT ck_resource_transactions_resourceaction CHECK (kind IN ('issue', 'consume', 'refund')),
    CONSTRAINT ck_resource_transactions_resourcekind CHECK (resource_kind IN ('compute', 'admin_utilities', 'seats')),
    CONSTRAINT fk_resource_transactions_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("""
CREATE INDEX ix_resource_transaction_history ON resource_transactions (tenant_id, created_at, id)
""")
    op.execute("""
CREATE INDEX ix_resource_transactions_tenant_id ON resource_transactions (tenant_id)
""")
    enable_tenant_rls("resource_transactions")
    grant("resource_transactions", app="SELECT, INSERT", platform="SELECT")
    op.execute("""
CREATE TABLE resource_ledger_entries (
    transaction_id UUID NOT NULL,
    wallet_id UUID NOT NULL,
    resource_kind VARCHAR(32) NOT NULL,
    delta BIGINT NOT NULL,
    balance_after BIGINT NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_resource_ledger_entries PRIMARY KEY (id),
    CONSTRAINT uq_resource_entry_posting UNIQUE (transaction_id, wallet_id),
    CONSTRAINT fk_resource_ledger_entries_tenant_id_resource_transactions FOREIGN KEY(tenant_id, transaction_id,
        resource_kind) REFERENCES resource_transactions (tenant_id, id, resource_kind),
    CONSTRAINT fk_resource_ledger_entries_tenant_id_resource_wallets FOREIGN KEY(tenant_id, wallet_id, resource_kind)
        REFERENCES resource_wallets (tenant_id, id, resource_kind),
    CONSTRAINT ck_resource_ledger_entries_resource_delta_nonzero CHECK (delta <> 0),
    CONSTRAINT ck_resource_ledger_entries_resource_entry_balance_range CHECK (balance_after BETWEEN 0 AND 9007199254740991),
    CONSTRAINT ck_resource_ledger_entries_resourcekind CHECK (resource_kind IN ('compute', 'admin_utilities', 'seats')),
    CONSTRAINT fk_resource_ledger_entries_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("""
CREATE INDEX ix_resource_entry_wallet ON resource_ledger_entries (tenant_id, wallet_id, created_at)
""")
    op.execute("""
CREATE INDEX ix_resource_ledger_entries_tenant_id ON resource_ledger_entries (tenant_id)
""")
    enable_tenant_rls("resource_ledger_entries")
    grant("resource_ledger_entries", app="SELECT, INSERT", platform="SELECT")
    op.execute("""
CREATE FUNCTION resource_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$
""")
    op.execute("""
CREATE TRIGGER resource_transactions_immutable BEFORE UPDATE OR DELETE ON resource_transactions
    FOR EACH ROW EXECUTE FUNCTION resource_immutable()
""")
    op.execute("""
CREATE TRIGGER resource_entries_immutable BEFORE UPDATE OR DELETE ON resource_ledger_entries
    FOR EACH ROW EXECUTE FUNCTION resource_immutable()
""")
    op.execute("""
CREATE TRIGGER resource_wallets_no_delete BEFORE DELETE ON resource_wallets
    FOR EACH ROW EXECUTE FUNCTION resource_immutable()
""")
    op.execute("""
CREATE FUNCTION resource_wallet_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.balance <> 0 THEN
            RAISE EXCEPTION 'Resource wallets must open at zero' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.user_id <> OLD.user_id
          OR NEW.resource_kind <> OLD.resource_kind THEN
        RAISE EXCEPTION 'Resource wallet identity is immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""")
    op.execute("""
CREATE TRIGGER resource_wallet_identity BEFORE INSERT OR UPDATE ON resource_wallets
    FOR EACH ROW EXECUTE FUNCTION resource_wallet_identity()
""")
    op.execute("""
CREATE FUNCTION resource_entry_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual bigint;
BEGIN
    SELECT balance INTO STRICT actual FROM resource_wallets
        WHERE tenant_id = NEW.tenant_id AND id = NEW.wallet_id AND resource_kind = NEW.resource_kind FOR UPDATE;
    IF NEW.balance_after <> actual THEN
        RAISE EXCEPTION 'Resource posting snapshot differs from locked balance' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$
""")
    op.execute("""
CREATE TRIGGER resource_entry_snapshot BEFORE INSERT ON resource_ledger_entries
    FOR EACH ROW EXECUTE FUNCTION resource_entry_snapshot()
""")
    op.execute("""
CREATE FUNCTION resource_balance_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    wallet uuid;
    actual bigint;
    posted numeric;
BEGIN
    IF TG_TABLE_NAME = 'resource_wallets' THEN wallet := NEW.id; ELSE wallet := NEW.wallet_id; END IF;
    SELECT balance INTO actual FROM resource_wallets WHERE tenant_id = NEW.tenant_id AND id = wallet;
    SELECT coalesce(sum(delta), 0) INTO posted FROM resource_ledger_entries
        WHERE tenant_id = NEW.tenant_id AND wallet_id = wallet;
    IF actual IS DISTINCT FROM posted THEN
        RAISE EXCEPTION 'Resource balance does not match ledger' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$
""")
    op.execute("""
CREATE CONSTRAINT TRIGGER resource_wallet_posted AFTER INSERT OR UPDATE ON resource_wallets
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION resource_balance_check()
""")
    op.execute("""
CREATE CONSTRAINT TRIGGER resource_entry_posted AFTER INSERT ON resource_ledger_entries
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION resource_balance_check()
""")
    op.execute("""
CREATE FUNCTION resource_transaction_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    tx resource_transactions%ROWTYPE;
    original resource_transactions%ROWTYPE;
    txid uuid;
    expected integer;
    n integer;
    correct integer;
BEGIN
    IF TG_TABLE_NAME = 'resource_transactions' THEN txid := NEW.id; ELSE txid := NEW.transaction_id; END IF;
    SELECT * INTO STRICT tx FROM resource_transactions WHERE tenant_id = NEW.tenant_id AND id = txid;
    expected := (CASE WHEN tx.source_wallet_id IS NULL THEN 0 ELSE 1 END)
              + (CASE WHEN tx.target_wallet_id IS NULL THEN 0 ELSE 1 END);
    SELECT count(*), count(*) FILTER (WHERE
        (wallet_id = tx.source_wallet_id AND delta = -tx.amount) OR
        (wallet_id = tx.target_wallet_id AND delta = tx.amount)) INTO n, correct
        FROM resource_ledger_entries WHERE tenant_id = NEW.tenant_id AND transaction_id = txid;
    IF n <> expected OR correct <> expected THEN
        RAISE EXCEPTION 'Resource transaction postings are incomplete or invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF tx.kind = 'refund' THEN
        SELECT * INTO STRICT original FROM resource_transactions WHERE tenant_id = tx.tenant_id AND id = tx.refund_of_id;
        IF original.kind <> 'consume' OR original.amount <> tx.amount
            OR original.source_wallet_id IS DISTINCT FROM tx.target_wallet_id THEN
            RAISE EXCEPTION 'Invalid resource usage refund' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END $$
""")
    op.execute("""
CREATE CONSTRAINT TRIGGER resource_transaction_complete AFTER INSERT ON resource_transactions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION resource_transaction_check()
""")
    op.execute("""
CREATE CONSTRAINT TRIGGER resource_entry_transaction AFTER INSERT ON resource_ledger_entries
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION resource_transaction_check()
""")
    # Provision only existing system roles. Custom roles retain their explicit privileges.
    # Temporarily lift FORCE for the schema owner's additive backfill, following 0014.
    # Tenant application policies remain enabled throughout the transaction.
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
INSERT INTO role_permissions (tenant_id, role_id, permission)
SELECT r.tenant_id, r.id, p.permission FROM roles r
CROSS JOIN (VALUES ('resources.configure'), ('resources.issue'), ('resources.consume'),
                   ('resources.audit')) AS p(permission)
WHERE r.is_system AND (
    r.name = 'tenant_admin' OR
    (r.name IN ('master_agent', 'agent', 'player') AND p.permission = 'resources.consume'))
ON CONFLICT DO NOTHING
""")
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Explicitly refuse to discard operational history. Isolated test teardown truncates its
    # disposable resource tables first, just as it does the older immutable ledgers.
    op.execute("""
ALTER TABLE resource_transactions NO FORCE ROW LEVEL SECURITY
""")
    op.execute("""
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM resource_transactions) THEN
        RAISE EXCEPTION 'Cannot downgrade a populated operational resource ledger';
    END IF;
END $$
""")
    # Preserve role assignments on rollback, as in 0014; absent routes grant nothing.
    for table in ("resource_ledger_entries", "resource_transactions", "resource_wallets", "resource_settings"):
        op.drop_table(table)
    for fn in (
        "resource_transaction_check",
        "resource_balance_check",
        "resource_entry_snapshot",
        "resource_wallet_identity",
        "resource_immutable",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
