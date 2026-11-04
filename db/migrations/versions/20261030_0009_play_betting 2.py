"""Simulated fight betting with play balances.

Play wallets are funded only by a system starting grant and their own settlements: nothing
links them to the demonstration points ledger, so no user can transfer into or adjust them.
The wallet ledger is append-only (the app role cannot UPDATE or DELETE entries), and bets and
books are never deleted by the application.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE betting_settings (
    tenant_id UUID NOT NULL,
    enabled BOOLEAN NOT NULL,
    commission_rate NUMERIC(5, 4) NOT NULL,
    draw_multiplier NUMERIC(6, 2) NOT NULL,
    min_bet NUMERIC(14, 2) NOT NULL,
    max_bet NUMERIC(14, 2) NOT NULL,
    max_fight_stake NUMERIC(14, 2) NOT NULL,
    starting_balance NUMERIC(14, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_betting_settings PRIMARY KEY (tenant_id),
    CONSTRAINT ck_betting_settings_commission_range CHECK (commission_rate >= 0 AND commission_rate < 0.5),
    CONSTRAINT ck_betting_settings_draw_multiplier_min CHECK (draw_multiplier >= 1),
    CONSTRAINT ck_betting_settings_bet_limits CHECK (min_bet > 0 AND max_bet >= min_bet),
    CONSTRAINT ck_betting_settings_fight_limit CHECK (max_fight_stake >= max_bet),
    CONSTRAINT ck_betting_settings_starting_balance_non_negative CHECK (starting_balance >= 0),
    CONSTRAINT fk_betting_settings_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
)
""")
    enable_tenant_rls("betting_settings")
    grant("betting_settings", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE play_wallets (
    user_id UUID NOT NULL,
    balance NUMERIC(14, 2) NOT NULL,
    version INTEGER NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_play_wallets PRIMARY KEY (id),
    CONSTRAINT uq_play_wallets_tenant_id_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_play_wallets_tenant_user UNIQUE (tenant_id, user_id),
    CONSTRAINT fk_play_wallets_user FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_play_wallets_balance_non_negative CHECK (balance >= 0),
    CONSTRAINT fk_play_wallets_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_play_wallets_tenant_id ON play_wallets (tenant_id)")
    enable_tenant_rls("play_wallets")
    grant("play_wallets", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE fight_books (
    match_id UUID NOT NULL,
    state VARCHAR(32) NOT NULL,
    meron_pool NUMERIC(16, 2) NOT NULL,
    wala_pool NUMERIC(16, 2) NOT NULL,
    draw_pool NUMERIC(16, 2) NOT NULL,
    bet_count INTEGER NOT NULL,
    version INTEGER NOT NULL,
    commission_rate NUMERIC(5, 4) NOT NULL,
    opened_at TIMESTAMP WITH TIME ZONE,
    closed_at TIMESTAMP WITH TIME ZONE,
    settled_at TIMESTAMP WITH TIME ZONE,
    final_meron_odds NUMERIC(20, 10),
    final_wala_odds NUMERIC(20, 10),
    outcome VARCHAR(32),
    result_revision INTEGER,
    commission_amount NUMERIC(16, 2),
    settle_count INTEGER NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_fight_books PRIMARY KEY (id),
    CONSTRAINT uq_fight_books_match UNIQUE (tenant_id, match_id),
    CONSTRAINT fk_fight_books_match FOREIGN KEY(tenant_id, match_id) REFERENCES matches (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_fight_books_pools_non_negative CHECK (meron_pool >= 0 AND wala_pool >= 0 AND draw_pool >= 0),
    CONSTRAINT ck_fight_books_bookstate CHECK (state IN ('open', 'closed', 'settled')),
    CONSTRAINT fk_fight_books_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_fight_books_tenant_id ON fight_books (tenant_id)")
    enable_tenant_rls("fight_books")
    grant("fight_books", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE bets (
    match_id UUID NOT NULL,
    user_id UUID NOT NULL,
    request_id UUID NOT NULL,
    side VARCHAR(32) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    pricing VARCHAR(32) NOT NULL,
    multiplier NUMERIC(6, 2),
    status VARCHAR(32) NOT NULL,
    payout NUMERIC(16, 2) NOT NULL,
    credited NUMERIC(16, 2) NOT NULL,
    net_profit NUMERIC(16, 2),
    settled_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_bets PRIMARY KEY (id),
    CONSTRAINT uq_bets_request UNIQUE (tenant_id, user_id, request_id),
    CONSTRAINT fk_bets_user FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_bets_match FOREIGN KEY(tenant_id, match_id) REFERENCES matches (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_bets_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_bets_payout_non_negative CHECK (payout >= 0 AND credited >= 0),
    CONSTRAINT ck_bets_multiplier_matches_pricing CHECK ((pricing = 'fixed') = (multiplier IS NOT NULL)),
    CONSTRAINT ck_bets_draw_is_fixed CHECK ((side = 'draw') = (pricing = 'fixed')),
    CONSTRAINT ck_bets_betside CHECK (side IN ('meron', 'wala', 'draw')),
    CONSTRAINT ck_bets_betpricing CHECK (pricing IN ('pool', 'fixed')),
    CONSTRAINT ck_bets_betstatus CHECK (status IN ('accepted', 'won', 'lost', 'refunded')),
    CONSTRAINT fk_bets_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_bets_match_user ON bets (tenant_id, match_id, user_id)")
    op.execute("CREATE INDEX ix_bets_tenant_id ON bets (tenant_id)")
    op.execute("CREATE INDEX ix_bets_user_created ON bets (tenant_id, user_id, created_at)")
    enable_tenant_rls("bets")
    grant("bets", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE play_wallet_entries (
    wallet_id UUID NOT NULL,
    kind VARCHAR(32) NOT NULL,
    delta NUMERIC(16, 2) NOT NULL,
    balance_after NUMERIC(16, 2) NOT NULL,
    bet_id UUID,
    match_id UUID,
    idempotency_key VARCHAR(160) NOT NULL,
    memo VARCHAR(200),
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_play_wallet_entries PRIMARY KEY (id),
    CONSTRAINT uq_play_wallet_entries_key UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_play_wallet_entries_wallet FOREIGN KEY(tenant_id, wallet_id) REFERENCES play_wallets (tenant_id, id),
    CONSTRAINT ck_play_wallet_entries_delta_non_zero CHECK (delta <> 0),
    CONSTRAINT ck_play_wallet_entries_balance_after_non_negative CHECK (balance_after >= 0),
    CONSTRAINT ck_play_wallet_entries_walletentrykind CHECK (kind IN ('grant', 'stake', 'payout', 'refund', 'resettlement')),
    CONSTRAINT fk_play_wallet_entries_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_play_wallet_entries_bet ON play_wallet_entries (bet_id)")
    op.execute("CREATE INDEX ix_play_wallet_entries_tenant_id ON play_wallet_entries (tenant_id)")
    op.execute("CREATE INDEX ix_play_wallet_entries_wallet_created ON play_wallet_entries (wallet_id, created_at)")
    enable_tenant_rls("play_wallet_entries")
    grant("play_wallet_entries", app="SELECT, INSERT", platform="SELECT")


def downgrade() -> None:
    op.drop_table("play_wallet_entries")
    op.drop_table("bets")
    op.drop_table("fight_books")
    op.drop_table("play_wallets")
    op.drop_table("betting_settings")
