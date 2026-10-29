"""Declarator event assignments and audited result approval queue.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_tenant_users_accounttype"), "tenant_users", type_="check")
    op.create_check_constraint(
        "accounttype",
        "tenant_users",
        "account_type IN ('admin', 'operator', 'declarator', 'master_agent', 'agent', 'player')",
    )
    op.execute("""
CREATE TABLE event_declarators (
	event_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	assigned_by_id UUID NOT NULL, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	tenant_id UUID NOT NULL, 
	CONSTRAINT pk_event_declarators PRIMARY KEY (id), 
	CONSTRAINT uq_event_declarators_assignment UNIQUE (event_id, user_id), 
        CONSTRAINT fk_event_declarators_assigner FOREIGN KEY(tenant_id, assigned_by_id) REFERENCES tenant_users
        (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_event_declarators_tenant_id_events FOREIGN KEY(tenant_id, event_id) REFERENCES events
        (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_event_declarators_tenant_id_tenant_users FOREIGN KEY(tenant_id, user_id) REFERENCES
        tenant_users (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_event_declarators_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON
        DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_event_declarators_tenant_id ON event_declarators (tenant_id)")
    enable_tenant_rls("event_declarators")
    grant(
        "event_declarators",
        app="SELECT, INSERT, UPDATE, DELETE",
        platform="SELECT, INSERT, UPDATE, DELETE",
    )
    op.execute("""
CREATE TABLE result_declarations (
	match_id UUID NOT NULL, 
	request_id UUID NOT NULL, 
	outcome VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	base_revision INTEGER NOT NULL, 
	reason VARCHAR(500), 
	declared_by_id UUID NOT NULL, 
	approved_by_id UUID, 
	finalized_at TIMESTAMP WITH TIME ZONE, 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	tenant_id UUID NOT NULL, 
	CONSTRAINT pk_result_declarations PRIMARY KEY (id), 
	CONSTRAINT uq_result_declarations_tenant_id_id UNIQUE (tenant_id, id), 
	CONSTRAINT uq_result_declarations_request UNIQUE (tenant_id, request_id), 
        CONSTRAINT fk_declaration_match FOREIGN KEY(tenant_id, match_id) REFERENCES matches (tenant_id, id) ON
        DELETE RESTRICT,
        CONSTRAINT fk_declaration_author FOREIGN KEY(tenant_id, declared_by_id) REFERENCES tenant_users
        (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_declaration_approver FOREIGN KEY(tenant_id, approved_by_id) REFERENCES tenant_users
        (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT ck_result_declarations_declarationoutcome CHECK (outcome IN ('meron', 'wala', 'draw',
        'no_contest', 'cancelled')),
        CONSTRAINT ck_result_declarations_declarationstatus CHECK (status IN ('pending', 'declared',
        'pending_approval', 'under_review', 'finalized', 'cancelled')),
        CONSTRAINT fk_result_declarations_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON
        DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_result_declarations_tenant_id ON result_declarations (tenant_id)")
    op.execute("""
CREATE UNIQUE INDEX uq_result_declarations_active ON result_declarations (match_id) WHERE status IN
        ('pending_approval', 'under_review')
""")
    enable_tenant_rls("result_declarations")
    grant(
        "result_declarations",
        app="SELECT, INSERT, UPDATE",
        platform="SELECT, INSERT, UPDATE",
    )
    op.execute("""
CREATE TABLE declaration_audit (
	sequence INTEGER NOT NULL, 
	declaration_id UUID NOT NULL, 
	actor_id UUID NOT NULL, 
	action VARCHAR(50) NOT NULL, 
	from_status VARCHAR(32), 
	to_status VARCHAR(32) NOT NULL, 
	reason VARCHAR(500), 
	id UUID DEFAULT gen_random_uuid() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	tenant_id UUID NOT NULL, 
	CONSTRAINT pk_declaration_audit PRIMARY KEY (id), 
	CONSTRAINT uq_declaration_audit_sequence UNIQUE (declaration_id, sequence), 
        CONSTRAINT fk_declaration_audit_tenant_id_result_declarations FOREIGN KEY(tenant_id, declaration_id)
        REFERENCES result_declarations (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_declaration_audit_tenant_id_tenant_users FOREIGN KEY(tenant_id, actor_id) REFERENCES
        tenant_users (tenant_id, id) ON DELETE RESTRICT,
        CONSTRAINT fk_declaration_audit_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON
        DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_declaration_audit_tenant_id ON declaration_audit (tenant_id)")
    enable_tenant_rls("declaration_audit")
    grant("declaration_audit", app="SELECT, INSERT", platform="SELECT, INSERT")


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission IN ('results.submit', 'results.approve')")
    for table in ("declaration_audit", "result_declarations", "event_declarators"):
        op.drop_table(table)
    # Existing Declarator accounts are retained on rollback. NOT VALID preserves
    # those rows while disallowing new Declarator values until re-upgrade.
    op.drop_constraint(op.f("ck_tenant_users_accounttype"), "tenant_users", type_="check")
    op.execute("""
ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype CHECK (account_type IN ('admin', 'operator',
        'master_agent', 'agent', 'player')) NOT VALID
""")
