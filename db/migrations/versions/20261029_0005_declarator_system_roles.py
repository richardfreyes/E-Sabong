"""Backfill Declarator and approval permissions for existing tenants with forced RLS.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS result_verification_required BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_result_declarations_match_created "
        "ON result_declarations (tenant_id, match_id, created_at, id)"
    )
    # The migration role owns these tables but intentionally has NOBYPASSRLS.
    # NO FORCE only lets that owner perform this transactional backfill. RLS
    # remains enabled for the application role; FORCE is restored before commit.
    for table in (
        "tenants",
        "roles",
        "role_permissions",
        "events",
        "event_declarators",
        "result_declarations",
        "matches",
        "audit_logs",
    ):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute("""
        INSERT INTO roles (id, tenant_id, name, description, is_system)
        SELECT gen_random_uuid(), id, 'declarator', 'Submits results for assigned events', true FROM tenants
        ON CONFLICT (tenant_id, name) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, 'results.submit' FROM roles WHERE name = 'declarator' AND is_system
        UNION ALL
        SELECT tenant_id, id, 'results.approve' FROM roles WHERE name = 'tenant_admin' AND is_system
        UNION ALL
        SELECT tenant_id, id, 'results.submit' FROM roles WHERE name = 'tenant_admin' AND is_system
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        UPDATE events e SET result_verification_required = true
        WHERE EXISTS (SELECT 1 FROM event_declarators a WHERE a.tenant_id = e.tenant_id AND a.event_id = e.id)
           OR EXISTS (SELECT 1 FROM result_declarations d JOIN matches m ON m.id = d.match_id
                      WHERE d.tenant_id = e.tenant_id AND m.event_id = e.id)
           OR EXISTS (SELECT 1 FROM audit_logs a WHERE a.tenant_id = e.tenant_id AND a.resource_id = e.id::text
                      AND a.resource_type = 'event' AND a.action = 'declarator.assigned')
    """)
    for table in (
        "tenants",
        "roles",
        "role_permissions",
        "events",
        "event_declarators",
        "result_declarations",
        "matches",
        "audit_logs",
    ):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS result_verification_required")
    op.execute("DROP INDEX IF EXISTS ix_result_declarations_match_created")
    op.execute("ALTER TABLE role_permissions NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM role_permissions WHERE permission IN ('results.submit', 'results.approve')")
    op.execute("ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY")
