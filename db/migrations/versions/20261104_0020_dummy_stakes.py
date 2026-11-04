"""Declarator dummy stakes sit in the MERON/WALA pools without a player balance.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE fight_books ADD COLUMN dummy_meron numeric(16, 2) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE fight_books ADD COLUMN dummy_wala numeric(16, 2) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE fight_books ADD COLUMN dummy_request_ids uuid[] NOT NULL DEFAULT '{}'")
    op.execute(
        "ALTER TABLE fight_books ADD CONSTRAINT ck_fight_books_dummy_pools_non_negative "
        "CHECK (dummy_meron >= 0 AND dummy_wala >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE fight_books DROP CONSTRAINT ck_fight_books_dummy_pools_non_negative")
    op.execute("ALTER TABLE fight_books DROP COLUMN dummy_request_ids")
    op.execute("ALTER TABLE fight_books DROP COLUMN dummy_wala")
    op.execute("ALTER TABLE fight_books DROP COLUMN dummy_meron")
