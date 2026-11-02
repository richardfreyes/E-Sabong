"""Tenant video provider configuration and encrypted credentials."""

from alembic import op
from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE video_providers (
        tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        config JSONB NOT NULL, credentials TEXT, status VARCHAR(64) NOT NULL DEFAULT 'disabled',
        checked_at TIMESTAMPTZ
    )""")
    enable_tenant_rls("video_providers")
    grant("video_providers", app="SELECT, INSERT, UPDATE", platform="SELECT")


def downgrade():
    op.drop_table("video_providers")
