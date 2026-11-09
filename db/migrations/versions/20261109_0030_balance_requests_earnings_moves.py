"""Balance requests and moving one's own commission into one's own play wallet.

``balance_requests`` records a master agent or agent asking a Loader or administrator for play
balance. Nothing is posted until that approver decides. ``earnings_moves`` records commission
the same account moved into its own play wallet. Existing wallets, commission rows and requests
are not rewritten.

Revision ID: 0030
Revises: 0029
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant, revoke_all

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

ENTRY_KINDS = (
    "'grant', 'stake', 'payout', 'refund', 'resettlement', 'uniform_grant', 'reset', "
    "'correction', 'account_grant', 'transfer', 'earnings'"
)
PREVIOUS_KINDS = (
    "'grant', 'stake', 'payout', 'refund', 'resettlement', 'uniform_grant', 'reset', "
    "'correction', 'account_grant', 'transfer'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind "
        f"CHECK (kind IN ({ENTRY_KINDS}))"
    )
    op.execute("""
        CREATE TABLE balance_requests (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            tenant_id UUID NOT NULL,
            reference VARCHAR(32) NOT NULL,
            requester_id UUID NOT NULL,
            approver_id UUID,
            amount NUMERIC(16, 2) NOT NULL,
            status VARCHAR(32) NOT NULL,
            decided_at TIMESTAMP WITH TIME ZONE,
            decided_by_id UUID,
            idempotency_key VARCHAR(128) NOT NULL,
            request_hash VARCHAR(64) NOT NULL,
            CONSTRAINT pk_balance_requests PRIMARY KEY (id),
            CONSTRAINT uq_balance_requests_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_balance_requests_reference UNIQUE (tenant_id, reference),
            CONSTRAINT uq_balance_requests_key UNIQUE (tenant_id, idempotency_key),
            CONSTRAINT ck_balance_requests_amount CHECK (amount > 0),
            CONSTRAINT ck_balance_requests_balancerequeststatus CHECK (status IN ('pending', 'approved', 'rejected')),
            CONSTRAINT ck_balance_requests_decision CHECK (
                (status = 'pending') = (decided_at IS NULL AND decided_by_id IS NULL)
            ),
            CONSTRAINT fk_balance_requests_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
            CONSTRAINT fk_balance_requests_requester FOREIGN KEY (tenant_id, requester_id)
                REFERENCES tenant_users (tenant_id, id),
            CONSTRAINT fk_balance_requests_approver FOREIGN KEY (tenant_id, approver_id)
                REFERENCES tenant_users (tenant_id, id),
            CONSTRAINT fk_balance_requests_decided_by FOREIGN KEY (tenant_id, decided_by_id)
                REFERENCES tenant_users (tenant_id, id)
        )
    """)
    op.execute("CREATE INDEX ix_balance_requests_requester ON balance_requests (tenant_id, requester_id, created_at)")
    op.execute(
        "CREATE INDEX ix_balance_requests_approver ON balance_requests (tenant_id, approver_id, status, created_at)"
    )
    enable_tenant_rls("balance_requests")
    grant("balance_requests", app="SELECT, INSERT, UPDATE", platform="SELECT")

    op.execute("""
        CREATE TABLE earnings_moves (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            tenant_id UUID NOT NULL,
            reference VARCHAR(32) NOT NULL,
            user_id UUID NOT NULL,
            amount NUMERIC(16, 2) NOT NULL,
            earnings_before NUMERIC(16, 2) NOT NULL,
            earnings_after NUMERIC(16, 2) NOT NULL,
            wallet_after NUMERIC(16, 2) NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL,
            request_hash VARCHAR(64) NOT NULL,
            CONSTRAINT pk_earnings_moves PRIMARY KEY (id),
            CONSTRAINT uq_earnings_moves_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_earnings_moves_reference UNIQUE (tenant_id, reference),
            CONSTRAINT uq_earnings_moves_key UNIQUE (tenant_id, idempotency_key),
            CONSTRAINT ck_earnings_moves_amount CHECK (amount > 0),
            CONSTRAINT ck_earnings_moves_non_negative CHECK (earnings_after >= 0),
            CONSTRAINT ck_earnings_moves_math CHECK (earnings_after = earnings_before - amount),
            CONSTRAINT fk_earnings_moves_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
            CONSTRAINT fk_earnings_moves_user FOREIGN KEY (tenant_id, user_id)
                REFERENCES tenant_users (tenant_id, id)
        )
    """)
    op.execute("CREATE INDEX ix_earnings_moves_user ON earnings_moves (tenant_id, user_id, created_at)")
    enable_tenant_rls("earnings_moves")
    grant("earnings_moves", app="SELECT, INSERT", platform="SELECT")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM play_wallet_entries WHERE kind = 'earnings') THEN
                RAISE EXCEPTION 'earnings wallet entries exist';
            END IF;
        END $$
    """)
    revoke_all("earnings_moves")
    op.execute("DROP TABLE earnings_moves")
    revoke_all("balance_requests")
    op.execute("DROP TABLE balance_requests")
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind "
        f"CHECK (kind IN ({PREVIOUS_KINDS}))"
    )
