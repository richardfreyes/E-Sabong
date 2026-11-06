"""Declarator automatic ghost betting: one active job per event, run by the API's ghost runner.

Ghost amounts go to the fight's dummy pools only and never touch a player balance.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant, revoke_all

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ghost_auto_bets (
    event_id UUID NOT NULL,
    match_id UUID NOT NULL,
    started_by_id UUID NOT NULL,
    meron_amount NUMERIC(14, 2) NOT NULL,
    wala_amount NUMERIC(14, 2) NOT NULL,
    interval_seconds INTEGER NOT NULL,
    status VARCHAR(16) DEFAULT 'active' NOT NULL,
    runs INTEGER DEFAULT 0 NOT NULL,
    added_meron NUMERIC(16, 2) DEFAULT 0 NOT NULL,
    added_wala NUMERIC(16, 2) DEFAULT 0 NOT NULL,
    next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
    stopped_at TIMESTAMP WITH TIME ZONE,
    stop_reason VARCHAR(200),
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_ghost_auto_bets PRIMARY KEY (id),
    CONSTRAINT fk_ghost_auto_bets_event FOREIGN KEY(tenant_id, event_id) REFERENCES events (tenant_id, id),
    CONSTRAINT fk_ghost_auto_bets_match FOREIGN KEY(tenant_id, match_id) REFERENCES matches (tenant_id, id),
    CONSTRAINT fk_ghost_auto_bets_started_by FOREIGN KEY(tenant_id, started_by_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT ck_ghost_auto_bets_amounts CHECK (
        meron_amount >= 0 AND wala_amount >= 0 AND (meron_amount > 0 OR wala_amount > 0)),
    CONSTRAINT ck_ghost_auto_bets_interval CHECK (interval_seconds BETWEEN 1 AND 3600),
    CONSTRAINT ck_ghost_auto_bets_status CHECK (status IN ('active', 'stopped')),
    CONSTRAINT fk_ghost_auto_bets_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_ghost_auto_bets_tenant_id ON ghost_auto_bets (tenant_id)")
    # At most one running job per event: a second Start is refused instead of doubling the rate.
    op.execute(
        "CREATE UNIQUE INDEX uq_ghost_auto_bets_active_event ON ghost_auto_bets (tenant_id, event_id) WHERE status = 'active'"
    )
    op.execute("CREATE INDEX ix_ghost_auto_bets_due ON ghost_auto_bets (next_run_at) WHERE status = 'active'")
    enable_tenant_rls("ghost_auto_bets")
    # The runner finds due jobs across tenants with the platform role, then acts inside each tenant.
    grant("ghost_auto_bets", app="SELECT, INSERT, UPDATE", platform="SELECT")


def downgrade() -> None:
    revoke_all("ghost_auto_bets")
    op.execute("DROP TABLE ghost_auto_bets")
