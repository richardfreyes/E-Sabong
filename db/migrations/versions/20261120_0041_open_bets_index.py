"""Index each player's open (unsettled) bets for live-video eligibility.

Stream access is ``balance + open stakes >= 100``. The MediaMTX reconciler checks every viewer
every few seconds, so the open-stake sum must read only the few unsettled rows of a player,
never their whole betting history.

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY: building the index must not block bet placement on a live database.
    with op.get_context().autocommit_block():
        op.execute("SET statement_timeout = 0")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_bets_open_user "
            "ON bets (tenant_id, user_id) INCLUDE (amount) WHERE status = 'accepted'"
        )
        op.execute("RESET statement_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_bets_open_user")
