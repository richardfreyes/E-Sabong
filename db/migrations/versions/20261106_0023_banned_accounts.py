"""Tenant accounts can be banned: a permanent block that only an administrator sets or lifts.

"suspended" stays the reversible block an agent or master agent may apply to their own downline.

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accountstatus")
    op.execute(
        "ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accountstatus "
        "CHECK (status IN ('active', 'suspended', 'banned'))"
    )


def downgrade() -> None:
    # A banned account becomes suspended: still blocked, in the narrower set of statuses.
    # The migration role does not bypass RLS, so lift FORCE for this cross-tenant data fix.
    op.execute("ALTER TABLE tenant_users NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE tenant_users SET status = 'suspended' WHERE status = 'banned'")
    op.execute("ALTER TABLE tenant_users FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accountstatus")
    op.execute("ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accountstatus CHECK (status IN ('active', 'suspended'))")
