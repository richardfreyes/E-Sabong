BEGIN;

-- Running upgrade 0003 -> 0004

ALTER TABLE tenant_users DROP CONSTRAINT ck_tenant_users_accounttype;

ALTER TABLE tenant_users ADD CONSTRAINT ck_tenant_users_accounttype CHECK (account_type IN ('admin', 'operator', 'declarator', 'master_agent', 'agent', 'player'));

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
);

CREATE INDEX ix_event_declarators_tenant_id ON event_declarators (tenant_id);

ALTER TABLE "event_declarators" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "event_declarators" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "event_declarators" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT, UPDATE, DELETE ON "event_declarators" TO esabong_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON "event_declarators" TO esabong_platform;

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
);

CREATE INDEX ix_result_declarations_tenant_id ON result_declarations (tenant_id);

CREATE UNIQUE INDEX uq_result_declarations_active ON result_declarations (match_id) WHERE status IN
        ('pending_approval', 'under_review');

ALTER TABLE "result_declarations" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "result_declarations" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "result_declarations" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT, UPDATE ON "result_declarations" TO esabong_app;

GRANT SELECT, INSERT, UPDATE ON "result_declarations" TO esabong_platform;

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
);

CREATE INDEX ix_declaration_audit_tenant_id ON declaration_audit (tenant_id);

ALTER TABLE "declaration_audit" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "declaration_audit" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "declaration_audit" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT ON "declaration_audit" TO esabong_app;

GRANT SELECT, INSERT ON "declaration_audit" TO esabong_platform;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';

-- Running upgrade 0004 -> 0005

ALTER TABLE events ADD COLUMN IF NOT EXISTS result_verification_required BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS ix_result_declarations_match_created ON result_declarations (tenant_id, match_id, created_at, id);

ALTER TABLE "tenants" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "roles" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "role_permissions" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "events" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "event_declarators" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "result_declarations" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "matches" NO FORCE ROW LEVEL SECURITY;

ALTER TABLE "audit_logs" NO FORCE ROW LEVEL SECURITY;

INSERT INTO roles (id, tenant_id, name, description, is_system)
        SELECT gen_random_uuid(), id, 'declarator', 'Submits results for assigned events', true FROM tenants
        ON CONFLICT (tenant_id, name) DO NOTHING;

INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, 'results.submit' FROM roles WHERE name = 'declarator' AND is_system
        UNION ALL
        SELECT tenant_id, id, 'results.approve' FROM roles WHERE name = 'tenant_admin' AND is_system
        UNION ALL
        SELECT tenant_id, id, 'results.submit' FROM roles WHERE name = 'tenant_admin' AND is_system
        ON CONFLICT DO NOTHING;

UPDATE events e SET result_verification_required = true
        WHERE EXISTS (SELECT 1 FROM event_declarators a WHERE a.tenant_id = e.tenant_id AND a.event_id = e.id)
           OR EXISTS (SELECT 1 FROM result_declarations d JOIN matches m ON m.id = d.match_id
                      WHERE d.tenant_id = e.tenant_id AND m.event_id = e.id)
           OR EXISTS (SELECT 1 FROM audit_logs a WHERE a.tenant_id = e.tenant_id AND a.resource_id = e.id::text
                      AND a.resource_type = 'event' AND a.action = 'declarator.assigned');

ALTER TABLE "tenants" FORCE ROW LEVEL SECURITY;

ALTER TABLE "roles" FORCE ROW LEVEL SECURITY;

ALTER TABLE "role_permissions" FORCE ROW LEVEL SECURITY;

ALTER TABLE "events" FORCE ROW LEVEL SECURITY;

ALTER TABLE "event_declarators" FORCE ROW LEVEL SECURITY;

ALTER TABLE "result_declarations" FORCE ROW LEVEL SECURITY;

ALTER TABLE "matches" FORCE ROW LEVEL SECURITY;

ALTER TABLE "audit_logs" FORCE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';

COMMIT;

