"""What a balance request is asking for.

``request`` asks a Loader or Super Admin to add play balance. ``cashout`` asks them to
approve taking the requester's own main wallet down. ``earnings`` asks them to approve
taking available commission down. Convert-to-wallet is not a request.

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE balance_requests ADD COLUMN purpose VARCHAR(16) NOT NULL DEFAULT 'request'")
    op.execute(
        "ALTER TABLE balance_requests ADD CONSTRAINT ck_balance_requests_purpose "
        "CHECK (purpose IN ('request', 'cashout', 'earnings'))"
    )
    op.execute("ALTER TABLE balance_requests ALTER COLUMN purpose DROP DEFAULT")


def downgrade() -> None:
    op.execute("ALTER TABLE balance_requests DROP CONSTRAINT ck_balance_requests_purpose")
    op.execute("ALTER TABLE balance_requests DROP COLUMN purpose")
