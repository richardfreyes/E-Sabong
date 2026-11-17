"""Move unbounded ghost retry arrays off the hot fight-book row.

Revision ID: 0040
Revises: 0039
"""

from alembic import op
from app.core.migration_helpers import enable_tenant_rls, grant, revoke_all

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Production keeps a 30s statement_timeout. Backfilling the historical
    # retry ids (about 200k on the live database) does not fit in that limit.
    # SET LOCAL lasts only for this migration transaction.
    op.execute("SET LOCAL statement_timeout = 0")
    op.execute("""
        CREATE TABLE dummy_stake_receipts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            match_id UUID NOT NULL,
            request_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dummy_stake_receipts_request UNIQUE (tenant_id, match_id, request_id),
            FOREIGN KEY (tenant_id, match_id) REFERENCES matches(tenant_id, id) ON DELETE RESTRICT
        )
    """)
    op.execute(
        "CREATE INDEX ix_dummy_stake_receipts_tenant_id ON dummy_stake_receipts(tenant_id)"
    )
    op.execute("ALTER TABLE fight_books NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO dummy_stake_receipts(tenant_id, match_id, request_id)
        SELECT tenant_id, match_id, unnest(dummy_request_ids) FROM fight_books
        ON CONFLICT (tenant_id, match_id, request_id) DO NOTHING
    """)
    op.execute(
        "UPDATE fight_books SET dummy_request_ids = '{}' WHERE cardinality(dummy_request_ids) > 0"
    )
    op.execute("ALTER TABLE fight_books FORCE ROW LEVEL SECURITY")
    enable_tenant_rls("dummy_stake_receipts")
    grant("dummy_stake_receipts", app="SELECT, INSERT", platform="SELECT")


def downgrade() -> None:
    op.execute("SET LOCAL statement_timeout = 0")
    op.execute("ALTER TABLE fight_books NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dummy_stake_receipts NO FORCE ROW LEVEL SECURITY")
    # Restore every receipt before removing the table, retaining retry safety on rollback.
    op.execute("""
        UPDATE fight_books b SET dummy_request_ids = r.ids
        FROM (SELECT tenant_id, match_id, array_agg(request_id ORDER BY created_at, id) AS ids
              FROM dummy_stake_receipts GROUP BY tenant_id, match_id) r
        WHERE b.tenant_id = r.tenant_id AND b.match_id = r.match_id
    """)
    op.execute("ALTER TABLE fight_books FORCE ROW LEVEL SECURITY")
    revoke_all("dummy_stake_receipts")
    op.execute("DROP TABLE dummy_stake_receipts")
