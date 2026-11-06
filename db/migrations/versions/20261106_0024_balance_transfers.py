"""Hierarchy wallet transfers: a master agent or agent moves existing wallet points to a player.

The ledger kind ``transfer`` is a debit on the sender and a credit on the player. The pair is one
``balance_transfers`` row. Commission earnings are not involved, and the transfer creates no points.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

ENTRY_KINDS = (
    "'grant', 'stake', 'payout', 'refund', 'resettlement', 'uniform_grant', 'reset', "
    "'correction', 'account_grant', 'transfer'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        f"ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind CHECK (kind IN ({ENTRY_KINDS}))"
    )
    op.execute("""
CREATE TABLE balance_transfers (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    reference VARCHAR(32) NOT NULL,
    sender_id UUID NOT NULL,
    player_id UUID NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    sender_balance_before NUMERIC(14, 2) NOT NULL,
    sender_balance_after NUMERIC(14, 2) NOT NULL,
    player_balance_before NUMERIC(14, 2) NOT NULL,
    player_balance_after NUMERIC(14, 2) NOT NULL,
    status VARCHAR(16) DEFAULT 'applied' NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    CONSTRAINT pk_balance_transfers PRIMARY KEY (id),
    CONSTRAINT uq_balance_transfers_reference UNIQUE (tenant_id, reference),
    CONSTRAINT uq_balance_transfers_key UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT fk_balance_transfers_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
    CONSTRAINT fk_balance_transfers_sender FOREIGN KEY (tenant_id, sender_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT fk_balance_transfers_player FOREIGN KEY (tenant_id, player_id) REFERENCES tenant_users (tenant_id, id),
    CONSTRAINT ck_balance_transfers_amount CHECK (amount > 0),
    CONSTRAINT ck_balance_transfers_status CHECK (status = 'applied'),
    CONSTRAINT ck_balance_transfers_sender_math CHECK (
        sender_balance_after = sender_balance_before - amount AND sender_balance_before >= amount
    ),
    CONSTRAINT ck_balance_transfers_player_math CHECK (player_balance_after = player_balance_before + amount),
    CONSTRAINT ck_balance_transfers_non_negative CHECK (sender_balance_after >= 0 AND player_balance_before >= 0)
)
""")
    op.execute("CREATE INDEX ix_balance_transfers_tenant_id ON balance_transfers (tenant_id)")
    op.execute("CREATE INDEX ix_balance_transfers_sender ON balance_transfers (tenant_id, sender_id, created_at)")
    op.execute("CREATE INDEX ix_balance_transfers_player ON balance_transfers (tenant_id, player_id)")
    enable_tenant_rls("balance_transfers")
    grant("balance_transfers", app="SELECT, INSERT", platform="SELECT")


def downgrade() -> None:
    op.execute("ALTER TABLE balance_transfers NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE play_wallet_entries NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM balance_transfers)
               OR EXISTS (SELECT 1 FROM play_wallet_entries WHERE kind = 'transfer') THEN
                RAISE EXCEPTION 'balance transfers exist; refusing to downgrade';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE balance_transfers")
    op.execute("ALTER TABLE play_wallet_entries DROP CONSTRAINT ck_play_wallet_entries_walletentrykind")
    op.execute(
        "ALTER TABLE play_wallet_entries ADD CONSTRAINT ck_play_wallet_entries_walletentrykind CHECK (kind IN ("
        "'grant', 'stake', 'payout', 'refund', 'resettlement', 'uniform_grant', 'reset', 'correction', 'account_grant'))"
    )
    op.execute("ALTER TABLE play_wallet_entries FORCE ROW LEVEL SECURITY")
