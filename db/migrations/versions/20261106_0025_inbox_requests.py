"""Player requests an agent can approve or deny from My Inbox.

Approving a request moves existing play points from the player back to the direct upline.
Nothing is paid out as cash, and earnings are not involved.

Revision ID: 0025
Revises: 0024
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE inbox_requests (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    player_id UUID NOT NULL,
    upline_id UUID NOT NULL,
    kind VARCHAR(16) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    status VARCHAR(16) DEFAULT 'pending' NOT NULL,
    decided_by_id UUID,
    decided_at TIMESTAMP WITH TIME ZONE,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    CONSTRAINT pk_inbox_requests PRIMARY KEY (id),
    CONSTRAINT uq_inbox_requests_key UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_inbox_requests_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
    CONSTRAINT fk_inbox_requests_player FOREIGN KEY (tenant_id, player_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT fk_inbox_requests_upline FOREIGN KEY (tenant_id, upline_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT fk_inbox_requests_decided_by FOREIGN KEY (tenant_id, decided_by_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT ck_inbox_requests_kind CHECK (kind = 'cashout'),
    CONSTRAINT ck_inbox_requests_amount CHECK (amount > 0),
    CONSTRAINT ck_inbox_requests_status CHECK (status IN ('pending', 'approved', 'denied')),
    CONSTRAINT ck_inbox_requests_decision CHECK ((status = 'pending') = (decided_at IS NULL AND decided_by_id IS NULL))
)
""")
    op.execute(
        "CREATE UNIQUE INDEX uq_inbox_requests_open ON inbox_requests (tenant_id, player_id) "
        "WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX ix_inbox_requests_tenant_id ON inbox_requests (tenant_id)")
    op.execute("CREATE INDEX ix_inbox_requests_upline ON inbox_requests (tenant_id, upline_id, status, created_at)")
    enable_tenant_rls("inbox_requests")
    grant("inbox_requests", app="SELECT, INSERT, UPDATE", platform="SELECT")


def downgrade() -> None:
    op.execute("ALTER TABLE inbox_requests NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM inbox_requests) THEN
                RAISE EXCEPTION 'inbox requests exist; refusing to downgrade';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE inbox_requests")
