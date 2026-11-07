"""Stream Operator accounts, their stream assignments, broadcast sessions and recordings.

Adds the ``stream_operator`` account type and system role (permission ``streams.operate``, also
granted to existing tenant administrator roles), and three tenant-scoped tables. Existing rows
are only read for the role backfill; streams, events, wallets and bets are untouched.

Revision ID: 0027
Revises: 0026
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

ACCOUNT_TYPES = "'admin', 'operator', 'declarator', 'master_agent', 'agent', 'player'"


def upgrade() -> None:
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype")
    op.execute(
        "ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype "
        f"CHECK (account_type IN ({ACCOUNT_TYPES}, 'stream_operator'))"
    )
    # Target of the composite foreign keys below: rows can only reference streams of their tenant.
    op.execute("ALTER TABLE video_streams ADD CONSTRAINT uq_video_streams_tenant_id_id UNIQUE (tenant_id, id)")

    op.execute("""
CREATE TABLE video_stream_operators (
    tenant_id UUID NOT NULL,
    stream_id UUID NOT NULL,
    user_id UUID NOT NULL,
    assigned_by_id UUID,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_video_stream_operators PRIMARY KEY (id),
    CONSTRAINT uq_video_stream_operators_assignment UNIQUE (stream_id, user_id),
    CONSTRAINT fk_video_stream_operators_tenant FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    CONSTRAINT fk_video_stream_operators_stream FOREIGN KEY(tenant_id, stream_id)
        REFERENCES video_streams (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_video_stream_operators_user FOREIGN KEY(tenant_id, user_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_video_stream_operators_assigner FOREIGN KEY(tenant_id, assigned_by_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE SET NULL (assigned_by_id)
)
""")
    op.execute("CREATE INDEX ix_video_stream_operators_tenant_user ON video_stream_operators (tenant_id, user_id)")
    enable_tenant_rls("video_stream_operators")
    # The platform role reads assignments while answering MediaMTX publish callbacks.
    grant("video_stream_operators", app="SELECT, INSERT, DELETE", platform="SELECT")

    op.execute("""
CREATE TABLE video_broadcasts (
    tenant_id UUID NOT NULL,
    stream_id UUID,
    stream_name VARCHAR(120) NOT NULL,
    operator_id UUID,
    method VARCHAR(16) NOT NULL,
    resolution VARCHAR(8),
    status VARCHAR(16) DEFAULT 'connecting' NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    live_at TIMESTAMP WITH TIME ZONE,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    end_reason VARCHAR(160),
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_video_broadcasts PRIMARY KEY (id),
    CONSTRAINT ck_video_broadcasts_method CHECK (method IN ('browser', 'obs', 'external', 'test')),
    CONSTRAINT ck_video_broadcasts_resolution CHECK (resolution IS NULL OR resolution IN ('720p', '1080p')),
    CONSTRAINT ck_video_broadcasts_status CHECK (status IN ('connecting', 'live', 'ended')),
    CONSTRAINT ck_video_broadcasts_ended CHECK ((status = 'ended') = (ended_at IS NOT NULL)),
    CONSTRAINT fk_video_broadcasts_tenant FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    CONSTRAINT fk_video_broadcasts_stream FOREIGN KEY(tenant_id, stream_id)
        REFERENCES video_streams (tenant_id, id) ON DELETE SET NULL (stream_id),
    CONSTRAINT fk_video_broadcasts_operator FOREIGN KEY(tenant_id, operator_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE SET NULL (operator_id)
)
""")
    op.execute("CREATE INDEX ix_video_broadcasts_tenant_started ON video_broadcasts (tenant_id, started_at DESC)")
    # At most one open session per stream: the reconciliation task and operators race safely.
    op.execute("CREATE UNIQUE INDEX uq_video_broadcasts_open ON video_broadcasts (stream_id) WHERE status <> 'ended'")
    enable_tenant_rls("video_broadcasts")
    grant("video_broadcasts", app="SELECT, INSERT, UPDATE", platform="SELECT, INSERT, UPDATE")

    op.execute("""
CREATE TABLE video_recordings (
    tenant_id UUID NOT NULL,
    stream_id UUID,
    stream_name VARCHAR(120) NOT NULL,
    started_by_id UUID,
    stopped_by_id UUID,
    status VARCHAR(16) DEFAULT 'recording' NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    stopped_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER DEFAULT 0 NOT NULL,
    size_bytes BIGINT DEFAULT 0 NOT NULL,
    segments JSONB DEFAULT '[]'::jsonb NOT NULL,
    detail VARCHAR(240),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_video_recordings PRIMARY KEY (id),
    CONSTRAINT ck_video_recordings_status CHECK (status IN ('recording', 'finalizing', 'completed', 'failed')),
    CONSTRAINT ck_video_recordings_duration_non_negative CHECK (duration_seconds >= 0),
    CONSTRAINT ck_video_recordings_size_non_negative CHECK (size_bytes >= 0),
    CONSTRAINT ck_video_recordings_stopped CHECK (status = 'recording' OR stopped_at IS NOT NULL),
    CONSTRAINT fk_video_recordings_tenant FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    CONSTRAINT fk_video_recordings_stream FOREIGN KEY(tenant_id, stream_id)
        REFERENCES video_streams (tenant_id, id) ON DELETE SET NULL (stream_id),
    CONSTRAINT fk_video_recordings_starter FOREIGN KEY(tenant_id, started_by_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE SET NULL (started_by_id),
    CONSTRAINT fk_video_recordings_stopper FOREIGN KEY(tenant_id, stopped_by_id)
        REFERENCES tenant_users (tenant_id, id) ON DELETE SET NULL (stopped_by_id)
)
""")
    op.execute("CREATE INDEX ix_video_recordings_tenant_started ON video_recordings (tenant_id, started_at DESC)")
    op.execute("CREATE INDEX ix_video_recordings_expires ON video_recordings (expires_at)")
    # One running recording per stream (MediaMTX records a path into one directory at a time).
    op.execute("CREATE UNIQUE INDEX uq_video_recordings_active ON video_recordings (stream_id) WHERE status = 'recording'")
    enable_tenant_rls("video_recordings")
    # The platform role finalizes recordings, applies retention and stops them at the limits.
    grant("video_recordings", app="SELECT, INSERT, UPDATE, DELETE", platform="SELECT, INSERT, UPDATE, DELETE")

    # New system role for every tenant; administrators may also operate streams. The migration role
    # has NOBYPASSRLS, so FORCE is lifted on every table this backfill reads or writes (tenants too,
    # or the SELECT below sees no tenant at all) and restored before commit.
    for table in ("tenants", "roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO roles (id, tenant_id, name, description, is_system)
        SELECT gen_random_uuid(), id, 'stream_operator', 'Broadcasts and records assigned live streams only', true
        FROM tenants
        ON CONFLICT (tenant_id, name) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, 'streams.operate' FROM roles
        WHERE name IN ('stream_operator', 'tenant_admin') AND is_system
        ON CONFLICT DO NOTHING
    """)
    for table in ("tenants", "roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in ("video_recordings", "video_broadcasts", "video_stream_operators"):
        op.drop_table(table)
    op.execute("ALTER TABLE video_streams DROP CONSTRAINT IF EXISTS uq_video_streams_tenant_id_id")
    # The permission is withdrawn; the (now empty) stream_operator role row is kept so accounts
    # that hold it stay valid and a later re-upgrade restores the same role.
    op.execute("ALTER TABLE role_permissions NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM role_permissions WHERE permission = 'streams.operate'")
    op.execute("ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY")
    # Existing Stream Operator accounts are retained; NOT VALID keeps them while refusing new ones.
    op.execute("ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype")
    op.execute(
        f"ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype CHECK (account_type IN ({ACCOUNT_TYPES})) NOT VALID"
    )
