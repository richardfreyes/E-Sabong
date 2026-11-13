"""Tenant-specific Declarator quick-bet amounts.

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_settings", sa.Column("declarator_quick_bets", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_settings", "declarator_quick_bets")
