"""Agent and master agent commissions: rates, a per-bet snapshot, and an append-only earnings ledger.

Commission is earnings only; it never posts to a play wallet. Existing bets keep zero rates (no
snapshot was taken when they were accepted), so they earn no commission.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant, revoke_all

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE agent_commission_rates (
    user_id UUID NOT NULL,
    rate NUMERIC(6, 4) NOT NULL,
    updated_by_id UUID,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_agent_commission_rates PRIMARY KEY (id),
    CONSTRAINT uq_agent_commission_rates_user UNIQUE (tenant_id, user_id),
    CONSTRAINT fk_agent_commission_rates_user FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT fk_agent_commission_rates_updated_by FOREIGN KEY(tenant_id, updated_by_id)
        REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT ck_agent_commission_rates_rate_range CHECK (rate >= 0 AND rate <= 0.5),
    CONSTRAINT fk_agent_commission_rates_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_agent_commission_rates_tenant_id ON agent_commission_rates (tenant_id)")
    enable_tenant_rls("agent_commission_rates")
    grant("agent_commission_rates", app="SELECT, INSERT, UPDATE", platform="SELECT")

    # Target of the composite (tenant_id, bet_id) foreign key below: a ledger row can only name a
    # bet of its own tenant.
    op.execute("ALTER TABLE bets ADD CONSTRAINT uq_bets_tenant_id_id UNIQUE (tenant_id, id)")
    # Snapshot taken when a bet is accepted. Rates are fractions (0.0300 = 3%).
    op.execute("ALTER TABLE bets ADD COLUMN agent_id UUID")
    op.execute("ALTER TABLE bets ADD COLUMN master_agent_id UUID")
    op.execute("ALTER TABLE bets ADD COLUMN agent_rate NUMERIC(6, 4) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE bets ADD COLUMN master_rate NUMERIC(6, 4) NOT NULL DEFAULT 0")
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT ck_bets_commission_rates "
        "CHECK (agent_rate >= 0 AND master_rate >= 0 AND agent_rate + master_rate <= 0.5)"
    )
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT fk_bets_agent FOREIGN KEY (tenant_id, agent_id) REFERENCES tenant_users (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE bets ADD CONSTRAINT fk_bets_master_agent "
        "FOREIGN KEY (tenant_id, master_agent_id) REFERENCES tenant_users (tenant_id, id)"
    )

    op.execute("""
CREATE TABLE commission_entries (
    match_id UUID NOT NULL,
    bet_id UUID NOT NULL,
    seq INTEGER NOT NULL,
    player_id UUID NOT NULL,
    agent_id UUID,
    master_agent_id UUID,
    bet_amount NUMERIC(14, 2) NOT NULL,
    agent_rate NUMERIC(6, 4) NOT NULL,
    master_rate NUMERIC(6, 4) NOT NULL,
    agent_points NUMERIC(16, 2) NOT NULL,
    master_points NUMERIC(16, 2) NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    result_revision INTEGER,
    status VARCHAR(32) NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_commission_entries PRIMARY KEY (id),
    CONSTRAINT uq_commission_entries_bet_seq UNIQUE (tenant_id, bet_id, seq),
    CONSTRAINT fk_commission_entries_bet FOREIGN KEY(tenant_id, bet_id) REFERENCES bets (tenant_id, id),
    CONSTRAINT fk_commission_entries_match FOREIGN KEY(tenant_id, match_id) REFERENCES matches (tenant_id, id),
    CONSTRAINT fk_commission_entries_player FOREIGN KEY(tenant_id, player_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT fk_commission_entries_agent FOREIGN KEY(tenant_id, agent_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT fk_commission_entries_master_agent FOREIGN KEY(tenant_id, master_agent_id)
        REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT ck_commission_entries_bet_amount_positive CHECK (bet_amount > 0),
    CONSTRAINT ck_commission_entries_rates_non_negative CHECK (agent_rate >= 0 AND master_rate >= 0),
    CONSTRAINT ck_commission_entries_commissionstatus CHECK (status IN ('earned', 'reversed')),
    CONSTRAINT fk_commission_entries_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_commission_entries_tenant_id ON commission_entries (tenant_id)")
    op.execute("CREATE INDEX ix_commission_entries_agent ON commission_entries (tenant_id, agent_id, created_at)")
    op.execute("CREATE INDEX ix_commission_entries_master ON commission_entries (tenant_id, master_agent_id, created_at)")
    op.execute("CREATE INDEX ix_commission_entries_match ON commission_entries (tenant_id, match_id)")
    enable_tenant_rls("commission_entries")
    # Append-only: the application inserts and reads, never updates or deletes.
    grant("commission_entries", app="SELECT, INSERT", platform="SELECT")


def downgrade() -> None:
    revoke_all("commission_entries")
    op.execute("DROP TABLE commission_entries")
    op.execute("ALTER TABLE bets DROP CONSTRAINT fk_bets_master_agent")
    op.execute("ALTER TABLE bets DROP CONSTRAINT fk_bets_agent")
    op.execute("ALTER TABLE bets DROP CONSTRAINT ck_bets_commission_rates")
    op.execute("ALTER TABLE bets DROP COLUMN master_rate")
    op.execute("ALTER TABLE bets DROP COLUMN agent_rate")
    op.execute("ALTER TABLE bets DROP COLUMN master_agent_id")
    op.execute("ALTER TABLE bets DROP COLUMN agent_id")
    op.execute("ALTER TABLE bets DROP CONSTRAINT uq_bets_tenant_id_id")
    revoke_all("agent_commission_rates")
    op.execute("DROP TABLE agent_commission_rates")
