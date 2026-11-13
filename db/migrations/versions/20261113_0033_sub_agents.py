"""Sub-agent accounts and the third commission share on immutable bet snapshots and ledger rows.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

TYPES = "'admin','operator','declarator','master_agent','agent','player','stream_operator','loader'"


def upgrade() -> None:
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype")
    op.execute(
        f"ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype CHECK (account_type IN ({TYPES},'sub_agent'))"
    )
    for table in ("bets", "commission_entries"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN sub_agent_id UUID")
        op.execute(f"ALTER TABLE {table} ADD COLUMN sub_agent_rate NUMERIC(6,4) NOT NULL DEFAULT 0")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_sub_agent "
            "FOREIGN KEY (tenant_id,sub_agent_id) REFERENCES tenant_users (tenant_id,id)"
        )
    op.execute("ALTER TABLE commission_entries ADD COLUMN sub_agent_points NUMERIC(16,2) NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX ix_commission_entries_sub_agent ON commission_entries (tenant_id,sub_agent_id,created_at)")
    for table, constraint in (("bets", "commission_rates"), ("commission_entries", "rates_non_negative")):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_{constraint}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_{constraint} CHECK "
            "(agent_rate >= 0 AND master_rate >= 0 AND sub_agent_rate >= 0 "
            "AND agent_rate + master_rate + sub_agent_rate <= 1)"
        )
    # Existing tenants receive exactly the same downline permissions as their Agent role.
    for table in ("tenants", "roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO roles (id,tenant_id,name,description,is_system)
        SELECT gen_random_uuid(),id,'sub_agent','Manages directly registered players',true FROM tenants
        ON CONFLICT (tenant_id,name) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (tenant_id,role_id,permission)
        SELECT sub.tenant_id,sub.id,p.permission FROM roles sub
        JOIN roles agent ON agent.tenant_id=sub.tenant_id AND agent.name='agent' AND agent.is_system
        JOIN role_permissions p ON p.tenant_id=agent.tenant_id AND p.role_id=agent.id
        WHERE sub.name='sub_agent' AND sub.is_system ON CONFLICT DO NOTHING
    """)
    for table in ("tenants", "roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Existing sub-agent accounts remain; reverting code does not silently reparent them.
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype")
    op.execute(f"ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype CHECK (account_type IN ({TYPES})) NOT VALID")
    op.execute("DROP INDEX ix_commission_entries_sub_agent")
    for table, constraint in (("bets", "commission_rates"), ("commission_entries", "rates_non_negative")):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_{table}_{constraint}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_{constraint} CHECK "
            "(agent_rate >= 0 AND master_rate >= 0 AND agent_rate + master_rate <= 1)"
        )
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT fk_{table}_sub_agent")
        op.execute(f"ALTER TABLE {table} DROP COLUMN sub_agent_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN sub_agent_rate")
    op.execute("ALTER TABLE commission_entries DROP COLUMN sub_agent_points")
