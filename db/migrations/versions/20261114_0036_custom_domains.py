"""Optional custom domain controls and observed TLS health."""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE tenant_domains
        ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT true,
        ADD COLUMN ssl_status VARCHAR(20) NOT NULL DEFAULT 'pending',
        ADD COLUMN ssl_checked_at TIMESTAMPTZ,
        ADD COLUMN connection_error VARCHAR(500)""")


def downgrade():
    op.execute("""ALTER TABLE tenant_domains DROP COLUMN connection_error,
        DROP COLUMN ssl_checked_at, DROP COLUMN ssl_status, DROP COLUMN enabled""")
