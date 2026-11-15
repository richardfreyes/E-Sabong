"""Owner odds per tenant; preserve the settlement policy of historical bets."""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE betting_settings ADD COLUMN meron_odds numeric(10,4), ADD COLUMN wala_odds numeric(10,4)"
    )
    op.execute(
        "ALTER TABLE betting_settings ADD CONSTRAINT owner_odds_pair CHECK ((meron_odds IS NULL AND wala_odds IS NULL) OR (meron_odds IS NOT NULL AND wala_odds IS NOT NULL AND meron_odds >= 1 AND wala_odds >= 1 AND meron_odds <= 10 AND wala_odds <= 10))"
    )
    op.execute(
        "ALTER TABLE bets ADD COLUMN acceptance_priced boolean NOT NULL DEFAULT false"
    )
    # Preserve recorded prices on unsettled bets, without rewriting completed settlements.
    op.execute("ALTER TABLE bets NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE bets SET acceptance_priced = true "
        "WHERE status = 'accepted' AND multiplier IS NOT NULL AND multiplier >= 1"
    )
    op.execute("ALTER TABLE bets FORCE ROW LEVEL SECURITY")
    op.execute(
        "GRANT UPDATE (meron_odds, wala_odds, updated_at) ON betting_settings TO esabong_platform"
    )
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT acceptance_price_present CHECK (NOT acceptance_priced OR (multiplier IS NOT NULL AND multiplier >= 1))"
    )


def downgrade():
    op.execute(
        "REVOKE UPDATE (meron_odds, wala_odds, updated_at) ON betting_settings FROM esabong_platform"
    )
    op.execute(
        "ALTER TABLE bets DROP CONSTRAINT acceptance_price_present, DROP COLUMN acceptance_priced"
    )
    op.execute(
        "ALTER TABLE betting_settings DROP CONSTRAINT owner_odds_pair, DROP COLUMN meron_odds, DROP COLUMN wala_odds"
    )
