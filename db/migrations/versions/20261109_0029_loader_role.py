"""Loader system role and the append-only record of allocations it makes.

Adds the ``loader`` account type, the system role (view, grant, history — not
``users.manage`` or ``balance.manage``), and ``loader_grants``. Existing wallets,
accounts and balance operations are not rewritten. Points still live on play wallets:
an administrator funds a Loader, and a grant moves that balance to one recipient.

Revision ID: 0029
Revises: 0028
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant, revoke_all

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

ACCOUNT_TYPES = "'admin', 'operator', 'declarator', 'master_agent', 'agent', 'player', 'stream_operator'"


def upgrade() -> None:
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype")
    op.execute(
        "ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype "
        f"CHECK (account_type IN ({ACCOUNT_TYPES}, 'loader'))"
    )
    op.execute("""
        CREATE TABLE loader_grants (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            tenant_id UUID NOT NULL,
            reference VARCHAR(32) NOT NULL,
            loader_id UUID NOT NULL,
            recipient_id UUID NOT NULL,
            amount NUMERIC(14, 2) NOT NULL,
            reason VARCHAR(300) NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            request_hash VARCHAR(64) NOT NULL,
            loader_balance_after NUMERIC(14, 2) NOT NULL,
            recipient_balance_after NUMERIC(14, 2) NOT NULL,
            CONSTRAINT pk_loader_grants PRIMARY KEY (id),
            CONSTRAINT uq_loader_grants_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_loader_grants_reference UNIQUE (tenant_id, reference),
            CONSTRAINT uq_loader_grants_key UNIQUE (tenant_id, idempotency_key),
            CONSTRAINT ck_loader_grants_amount_positive CHECK (amount > 0),
            CONSTRAINT fk_loader_grants_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
            CONSTRAINT fk_loader_grants_loader FOREIGN KEY(tenant_id, loader_id)
                REFERENCES tenant_users (tenant_id, id),
            CONSTRAINT fk_loader_grants_recipient FOREIGN KEY(tenant_id, recipient_id)
                REFERENCES tenant_users (tenant_id, id)
        )
    """)
    op.execute("CREATE INDEX ix_loader_grants_loader_created ON loader_grants (tenant_id, loader_id, created_at DESC)")
    enable_tenant_rls("loader_grants")
    grant("loader_grants", app="SELECT, INSERT", platform="SELECT")

    for table in ("tenants", "roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO roles (id, tenant_id, name, description, is_system)
        SELECT gen_random_uuid(), id, 'loader',
               'Manages balance allocation for Master Agents, Agents, and Players.', true
        FROM tenants
        ON CONFLICT (tenant_id, name) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, permission FROM roles
        CROSS JOIN (VALUES ('loader.view'), ('loader.grant'), ('loader.history')) AS perms(permission)
        WHERE name = 'loader' AND is_system
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, permission FROM roles
        CROSS JOIN (VALUES ('loader.view'), ('loader.grant'), ('loader.history')) AS perms(permission)
        WHERE name = 'tenant_admin' AND is_system
        ON CONFLICT DO NOTHING
    """)
    for table in ("tenants", "roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    revoke_all("loader_grants")
    op.execute("DROP TABLE loader_grants")
    op.execute("ALTER TABLE role_permissions NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM role_permissions WHERE permission IN ('loader.view', 'loader.grant', 'loader.history')")
    op.execute("ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY")
    # Existing Loader accounts stay. NOT VALID keeps them and refuses new ones until re-upgrade.
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype")
    op.execute(
        "ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype "
        f"CHECK (account_type IN ({ACCOUNT_TYPES})) NOT VALID"
    )
