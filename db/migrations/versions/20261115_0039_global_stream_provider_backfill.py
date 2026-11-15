"""Designate the original workspace when migration 0038 could not see any tenant.

0038 already ran on databases whose forced row security hid tenants from the
migration role. The immutable trigger then blocked a later update, so this
repair lifts that policy, sets the flag only when nobody holds it, and restores
both the trigger and forced security.
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("tenants", "video_streams"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(
        """
        DO $$
        DECLARE
            designated uuid;
        BEGIN
            IF EXISTS (SELECT 1 FROM tenants WHERE is_global_stream_provider) THEN
                RETURN;
            END IF;
            SELECT id INTO designated
            FROM tenants
            WHERE deleted_at IS NULL
            ORDER BY created_at, id
            LIMIT 1;
            IF designated IS NULL THEN
                RETURN;
            END IF;
            ALTER TABLE tenants DISABLE TRIGGER global_stream_provider_immutable;
            UPDATE tenants SET is_global_stream_provider = true WHERE id = designated;
            ALTER TABLE tenants ENABLE TRIGGER global_stream_provider_immutable;

            UPDATE video_streams SET is_default = false
            WHERE tenant_id = designated AND EXISTS (
                SELECT 1 FROM rental_settings r
                JOIN video_streams s ON s.id = r.global_stream_id
                WHERE r.id = 1 AND s.tenant_id = designated
            );
            UPDATE video_streams SET is_default = true WHERE id = COALESCE(
                (SELECT s.id FROM video_streams s
                 JOIN rental_settings r ON r.global_stream_id = s.id
                 WHERE r.id = 1 AND s.tenant_id = designated),
                (SELECT s.id FROM video_streams s
                 WHERE s.tenant_id = designated
                 ORDER BY s.is_default DESC, s.created_at, s.id
                 LIMIT 1)
            );
        END $$
        """
    )
    for table in ("tenants", "video_streams"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade():
    # 0038 owns the column and the trigger. Removing the designation here would
    # either violate that trigger or forget which revision created it.
    pass
