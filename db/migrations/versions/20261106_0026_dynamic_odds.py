"""Meron and Wala lock the odds accepted with each bet. A Declarator may set the board price.

The two prices sum to at most 3.66. Existing DRAW multipliers keep their values.

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0009 created this check under the ck_ prefix. Pool bets now store the odds they locked.
    op.execute("ALTER TABLE bets DROP CONSTRAINT IF EXISTS multiplier_matches_pricing")
    op.execute("ALTER TABLE bets DROP CONSTRAINT IF EXISTS ck_bets_multiplier_matches_pricing")
    op.execute("ALTER TABLE bets ALTER COLUMN multiplier TYPE numeric(10, 4)")
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT fixed_has_multiplier CHECK ((pricing <> 'fixed') OR (multiplier IS NOT NULL))"
    )
    op.execute("ALTER TABLE fight_books ADD COLUMN meron_odds_override numeric(10, 4)")
    op.execute("ALTER TABLE fight_books ADD COLUMN wala_odds_override numeric(10, 4)")
    op.execute(
        "ALTER TABLE fight_books ADD CONSTRAINT ck_fight_books_odds_override CHECK ("
        "(meron_odds_override IS NULL AND wala_odds_override IS NULL) OR "
        "(meron_odds_override >= 1 AND wala_odds_override >= 1 AND meron_odds_override + wala_odds_override <= 3.66))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE fight_books DROP CONSTRAINT IF EXISTS ck_fight_books_odds_override")
    op.execute("ALTER TABLE fight_books DROP COLUMN IF EXISTS wala_odds_override")
    op.execute("ALTER TABLE fight_books DROP COLUMN IF EXISTS meron_odds_override")
    op.execute("ALTER TABLE bets DROP CONSTRAINT IF EXISTS fixed_has_multiplier")
    op.execute("ALTER TABLE bets DROP CONSTRAINT IF EXISTS ck_bets_multiplier_matches_pricing")
    op.execute("UPDATE bets SET multiplier = NULL WHERE pricing <> 'fixed'")
    op.execute("ALTER TABLE bets ALTER COLUMN multiplier TYPE numeric(6, 2)")
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT multiplier_matches_pricing "
        "CHECK ((pricing = 'fixed') = (multiplier IS NOT NULL))"
    )
