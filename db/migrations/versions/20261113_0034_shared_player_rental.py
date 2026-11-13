"""Shared player domain rental controls. Existing tenants and ledgers remain unchanged."""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE rental_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        shared_domain_enabled BOOLEAN NOT NULL DEFAULT false,
        default_tenant_id UUID REFERENCES tenants(id) ON DELETE RESTRICT,
        broadcast_tenant_id UUID REFERENCES tenants(id) ON DELETE RESTRICT,
        global_event_id UUID REFERENCES events(id) ON DELETE RESTRICT,
        global_stream_id UUID REFERENCES video_streams(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON rental_settings TO esabong_platform"
    )
    for table, privileges in OWNER_WRITES.items():
        op.execute(f"GRANT {privileges} ON {table} TO esabong_platform")
    op.execute("""CREATE FUNCTION prevent_account_tenant_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
                RAISE EXCEPTION 'Account tenant ownership is permanent' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER tenant_user_ownership_immutable BEFORE UPDATE OF tenant_id ON tenant_users
        FOR EACH ROW EXECUTE FUNCTION prevent_account_tenant_change()""")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS tenant_user_ownership_immutable ON tenant_users")
    op.execute("DROP FUNCTION IF EXISTS prevent_account_tenant_change()")
    for table, privileges in OWNER_WRITES.items():
        op.execute(f"REVOKE {privileges} ON {table} FROM esabong_platform")
    op.execute("DROP TABLE rental_settings")


# OWNER-only orchestration reuses the existing event and financial engines. Tenant
# application grants and RLS are unchanged; append-only ledgers never gain UPDATE.
OWNER_WRITES = {
    "predictions": "UPDATE",
    "fight_books": "INSERT, UPDATE",
    "betting_settings": "INSERT",
    "bets": "UPDATE",
    "play_wallets": "INSERT, UPDATE",
    "play_wallet_entries": "INSERT",
    "commission_entries": "INSERT",
    "video_streams": "INSERT, DELETE",
}
