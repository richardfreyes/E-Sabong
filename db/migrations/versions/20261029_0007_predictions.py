"""Server-side non-monetary fight predictions.

One row per player per fight. Rows are never deleted by the application (predictions are
history): the app role gets SELECT/INSERT/UPDATE only. Outcomes are recomputed from official
results; nothing here references demonstration points.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE predictions (
    user_id UUID NOT NULL,
    match_id UUID NOT NULL,
    side VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    changes INTEGER NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_predictions PRIMARY KEY (id),
    CONSTRAINT uq_predictions_player_fight UNIQUE (tenant_id, user_id, match_id),
    CONSTRAINT fk_predictions_player FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_predictions_match FOREIGN KEY(tenant_id, match_id) REFERENCES matches (tenant_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_predictions_predictionside CHECK (side IN ('meron', 'wala', 'draw')),
    CONSTRAINT ck_predictions_predictionstatus CHECK (status IN ('pending', 'win', 'lose', 'void')),
    CONSTRAINT ck_predictions_changes_non_negative CHECK (changes >= 0),
    CONSTRAINT fk_predictions_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_predictions_tenant_id ON predictions (tenant_id)")
    op.execute("CREATE INDEX ix_predictions_match ON predictions (tenant_id, match_id)")
    op.execute("CREATE INDEX ix_predictions_player_created ON predictions (tenant_id, user_id, created_at)")
    enable_tenant_rls("predictions")
    grant("predictions", app="SELECT, INSERT, UPDATE", platform="SELECT")


def downgrade() -> None:
    op.drop_table("predictions")
