"""invoice number sequence

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28 08:30:00+00:00
"""

from collections.abc import Sequence

from alembic import op

from app.core.migration_helpers import PLATFORM_ROLE

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE invoice_number_seq START 1001")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE invoice_number_seq TO {PLATFORM_ROLE}")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")
