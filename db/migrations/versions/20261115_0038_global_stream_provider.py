"""Return global broadcast control to the permanently designated original Admin."""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE tenants ADD COLUMN is_global_stream_provider BOOLEAN NOT NULL DEFAULT false"
    )
    # The migration role has NOBYPASSRLS. FORCE hides every tenant and stream, so the
    # designation below would match nothing and still commit.
    for table in ("tenants", "video_streams"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    # Designate the original surviving workspace, never an Owner-selected replacement
    # or a hardcoded numeric Admin ID.
    op.execute("""UPDATE tenants SET is_global_stream_provider = true WHERE id = (
        SELECT id FROM tenants WHERE deleted_at IS NULL
        ORDER BY created_at, id LIMIT 1
    )""")
    op.execute("""CREATE UNIQUE INDEX uq_global_stream_provider ON tenants (is_global_stream_provider)
        WHERE is_global_stream_provider""")
    # Retain the existing broadcast and OBS key. The Admin's default selector owns it now.
    op.execute("""UPDATE video_streams SET is_default = false WHERE tenant_id IN
        (SELECT id FROM tenants WHERE is_global_stream_provider) AND EXISTS (
            SELECT 1 FROM rental_settings r JOIN video_streams s ON s.id = r.global_stream_id
            JOIN tenants t ON t.id = s.tenant_id AND t.is_global_stream_provider WHERE r.id = 1
        )""")
    op.execute("""UPDATE video_streams SET is_default = true WHERE id = COALESCE(
        (SELECT s.id FROM video_streams s JOIN tenants t ON t.id = s.tenant_id AND t.is_global_stream_provider
         JOIN rental_settings r ON r.global_stream_id = s.id WHERE r.id = 1),
        (SELECT s.id FROM video_streams s JOIN tenants t ON t.id = s.tenant_id AND t.is_global_stream_provider
         ORDER BY s.is_default DESC, s.created_at, s.id LIMIT 1))""")
    for table in ("tenants", "video_streams"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE FUNCTION protect_global_stream_provider() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.is_global_stream_provider THEN
                RAISE EXCEPTION 'Global stream provider cannot be deleted' USING ERRCODE = '23514';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.is_global_stream_provider IS DISTINCT FROM OLD.is_global_stream_provider
           OR (OLD.is_global_stream_provider AND NEW.deleted_at IS NOT NULL) THEN
            RAISE EXCEPTION 'Global stream provider designation is permanent' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute("""CREATE TRIGGER global_stream_provider_immutable BEFORE UPDATE OR DELETE ON tenants
        FOR EACH ROW EXECUTE FUNCTION protect_global_stream_provider()""")


def downgrade():
    op.execute("DROP TRIGGER global_stream_provider_immutable ON tenants")
    op.execute("DROP FUNCTION protect_global_stream_provider()")
    op.execute("DROP INDEX uq_global_stream_provider")
    op.execute("ALTER TABLE tenants DROP COLUMN is_global_stream_provider")
