"""Live streams served by MediaMTX: tenant-scoped configuration, hashed publish keys, status.

Adds one table. No existing row is read or changed; the external video provider
(``video_providers``), events, wallets, wagers and settlement are untouched.

Revision ID: 0016
Revises: 0015
"""

from alembic import op
from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE video_streams (
    tenant_id UUID NOT NULL,
    name VARCHAR(120) NOT NULL,
    source_kind VARCHAR(16) NOT NULL,
    source_url TEXT,
    source_protocol VARCHAR(8),
    source_host VARCHAR(253),
    path_id VARCHAR(32) NOT NULL,
    publish_key_hash VARCHAR(64),
    publish_key_hint VARCHAR(8),
    key_rotated_at TIMESTAMP WITH TIME ZONE,
    event_id UUID,
    is_default BOOLEAN DEFAULT false NOT NULL,
    enabled BOOLEAN DEFAULT false NOT NULL,
    status VARCHAR(16) DEFAULT 'offline' NOT NULL,
    status_detail VARCHAR(240),
    status_changed_at TIMESTAMP WITH TIME ZONE,
    last_live_at TIMESTAMP WITH TIME ZONE,
    checked_at TIMESTAMP WITH TIME ZONE,
    viewers INTEGER DEFAULT 0 NOT NULL,
    tracks JSONB,
    reconnect_requested_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_video_streams PRIMARY KEY (id),
    CONSTRAINT uq_video_streams_path_id UNIQUE (path_id),
    CONSTRAINT ck_video_streams_source_kind CHECK (source_kind IN ('obs', 'external', 'test')),
    CONSTRAINT ck_video_streams_status CHECK (status IN ('offline', 'connecting', 'live', 'reconnecting', 'error')),
    CONSTRAINT ck_video_streams_name_nonempty CHECK (length(trim(name)) > 0),
    CONSTRAINT ck_video_streams_external_has_source CHECK (source_kind <> 'external' OR source_url IS NOT NULL),
    CONSTRAINT ck_video_streams_viewers_non_negative CHECK (viewers >= 0),
    CONSTRAINT fk_video_streams_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    CONSTRAINT fk_video_streams_event FOREIGN KEY(tenant_id, event_id) REFERENCES events (tenant_id, id) ON DELETE SET NULL (event_id)
)
""")
    op.execute("CREATE INDEX ix_video_streams_tenant_id ON video_streams (tenant_id)")
    op.execute("CREATE UNIQUE INDEX uq_video_streams_name ON video_streams (tenant_id, name)")
    op.execute("CREATE UNIQUE INDEX uq_video_streams_event ON video_streams (tenant_id, event_id) WHERE event_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX uq_video_streams_default ON video_streams (tenant_id) WHERE is_default")
    enable_tenant_rls("video_streams")
    # The platform role reads every stream to answer MediaMTX auth callbacks and updates
    # status/viewer counts from the reconciliation task; only tenant administrators write config.
    grant("video_streams", app="SELECT, INSERT, UPDATE, DELETE", platform="SELECT, UPDATE")


def downgrade() -> None:
    op.drop_table("video_streams")
