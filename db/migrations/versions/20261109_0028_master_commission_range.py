"""Master agent commission can be set anywhere from 0% to 100% (was capped at 50%).

Only the two range checks change. Stored rates, bet snapshots and commission entries are not
touched, so earnings already posted keep the rate they were earned at.

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _limits(limit: str) -> None:
    op.execute("ALTER TABLE agent_commission_rates DROP CONSTRAINT ck_agent_commission_rates_rate_range")
    op.execute(
        f"ALTER TABLE agent_commission_rates ADD CONSTRAINT ck_agent_commission_rates_rate_range CHECK (rate >= 0 AND rate <= {limit})"
    )
    op.execute("ALTER TABLE bets DROP CONSTRAINT ck_bets_commission_rates")
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT ck_bets_commission_rates "
        f"CHECK (agent_rate >= 0 AND master_rate >= 0 AND agent_rate + master_rate <= {limit})"
    )


def upgrade() -> None:
    _limits("1")


def downgrade() -> None:
    # Validation fails (and nothing changes) if a rate above 50% was saved or snapshotted since.
    _limits("0.5")
