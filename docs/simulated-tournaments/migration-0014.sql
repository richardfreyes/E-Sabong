BEGIN;

-- Running upgrade 0013 -> 0014

CREATE TABLE simulation_arenas (
    name VARCHAR(120) NOT NULL,
    enabled BOOLEAN DEFAULT 'false' NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_arenas PRIMARY KEY (id),
    CONSTRAINT uq_simulation_arenas_tenant_id_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_simulation_arenas_name UNIQUE (tenant_id, name),
    CONSTRAINT ck_simulation_arenas_name_nonempty CHECK (length(trim(name)) > 0),
    CONSTRAINT fk_simulation_arenas_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE INDEX ix_simulation_arenas_tenant_id ON simulation_arenas (tenant_id);

ALTER TABLE "simulation_arenas" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "simulation_arenas" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "simulation_arenas" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT, UPDATE ON "simulation_arenas" TO esabong_app;

GRANT SELECT ON "simulation_arenas" TO esabong_platform;

CREATE TABLE simulation_controllers (
    arena_id UUID NOT NULL,
    user_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_controllers PRIMARY KEY (arena_id, user_id),
    CONSTRAINT fk_simulation_controllers_arena FOREIGN KEY(tenant_id, arena_id) REFERENCES simulation_arenas (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_simulation_controllers_user FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_simulation_controllers_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE INDEX ix_simulation_controllers_tenant_id ON simulation_controllers (tenant_id);

ALTER TABLE "simulation_controllers" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "simulation_controllers" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "simulation_controllers" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT, DELETE ON "simulation_controllers" TO esabong_app;

GRANT SELECT ON "simulation_controllers" TO esabong_platform;

CREATE TABLE simulation_matches (
    arena_id UUID NOT NULL,
    number INTEGER NOT NULL,
    team_a VARCHAR(120) NOT NULL,
    team_b VARCHAR(120) NOT NULL,
    state VARCHAR(32) NOT NULL,
    outcome VARCHAR(32),
    version INTEGER NOT NULL,
    final_call_ends_at TIMESTAMP WITH TIME ZONE,
    simulation_fee_rate NUMERIC(5, 4) NOT NULL,
    team_a_pool NUMERIC(16, 2) NOT NULL,
    team_b_pool NUMERIC(16, 2) NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_matches PRIMARY KEY (id),
    CONSTRAINT uq_simulation_matches_tenant_id_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_simulation_matches_number UNIQUE (tenant_id, arena_id, number),
    CONSTRAINT fk_simulation_matches_arena FOREIGN KEY(tenant_id, arena_id) REFERENCES simulation_arenas (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_simulation_matches_number_version CHECK (number > 0 AND version >= 0),
    CONSTRAINT ck_simulation_matches_pools_non_negative CHECK (team_a_pool >= 0 AND team_b_pool >= 0),
    CONSTRAINT ck_simulation_matches_fee_range CHECK (simulation_fee_rate >= 0 AND simulation_fee_rate < 0.5),
    CONSTRAINT ck_simulation_matches_teams_nonempty CHECK (length(trim(team_a)) > 0 AND length(trim(team_b)) > 0),
    CONSTRAINT ck_simulation_matches_result_matches_state CHECK ((state IN ('result_declared', 'settled')) = (outcome IS NOT NULL)),
    CONSTRAINT ck_simulation_matches_simulationstate CHECK (state IN ('pending', 'wagering_open', 'final_call', 'wagering_closed', 'simulation_running', 'result_declared', 'settled')),
    CONSTRAINT ck_simulation_matches_simulationoutcome CHECK (outcome IN ('team_a', 'team_b', 'draw', 'cancelled')),
    CONSTRAINT fk_simulation_matches_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE INDEX ix_simulation_matches_tenant_id ON simulation_matches (tenant_id);

ALTER TABLE "simulation_matches" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "simulation_matches" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "simulation_matches" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT, UPDATE ON "simulation_matches" TO esabong_app;

GRANT SELECT ON "simulation_matches" TO esabong_platform;

CREATE TABLE simulation_stakes (
    match_id UUID NOT NULL,
    player_id UUID NOT NULL,
    request_id UUID NOT NULL,
    side VARCHAR(32) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_stakes PRIMARY KEY (id),
    CONSTRAINT uq_simulation_stakes_tenant_id_id UNIQUE (tenant_id, id),
    CONSTRAINT uq_simulation_stakes_request UNIQUE (tenant_id, player_id, request_id),
    CONSTRAINT fk_simulation_stakes_match FOREIGN KEY(tenant_id, match_id) REFERENCES simulation_matches (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_simulation_stakes_wallet FOREIGN KEY(tenant_id, player_id) REFERENCES play_wallets (tenant_id, user_id) ON DELETE RESTRICT,
    CONSTRAINT ck_simulation_stakes_amount_positive CHECK (amount > 0 AND amount <> 'NaN'::numeric),
    CONSTRAINT ck_simulation_stakes_simulationside CHECK (side IN ('team_a', 'team_b')),
    CONSTRAINT fk_simulation_stakes_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE INDEX ix_simulation_stakes_tenant_id ON simulation_stakes (tenant_id);

CREATE INDEX ix_simulation_stakes_match ON simulation_stakes (tenant_id, match_id);

ALTER TABLE "simulation_stakes" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "simulation_stakes" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "simulation_stakes" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT ON "simulation_stakes" TO esabong_app;

GRANT SELECT ON "simulation_stakes" TO esabong_platform;

CREATE TABLE simulation_commands (
    match_id UUID NOT NULL,
    actor_id UUID NOT NULL,
    request_id UUID NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    version INTEGER NOT NULL,
    response JSONB NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_commands PRIMARY KEY (id),
    CONSTRAINT uq_simulation_commands_request UNIQUE (tenant_id, actor_id, request_id),
    CONSTRAINT fk_simulation_commands_match FOREIGN KEY(tenant_id, match_id) REFERENCES simulation_matches (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_simulation_commands_actor FOREIGN KEY(tenant_id, actor_id) REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_simulation_commands_request_hash_length CHECK (length(request_hash) = 64),
    CONSTRAINT ck_simulation_commands_version_non_negative CHECK (version >= 0),
    CONSTRAINT fk_simulation_commands_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE INDEX ix_simulation_commands_tenant_id ON simulation_commands (tenant_id);

ALTER TABLE "simulation_commands" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "simulation_commands" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "simulation_commands" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT ON "simulation_commands" TO esabong_app;

GRANT SELECT ON "simulation_commands" TO esabong_platform;

CREATE FUNCTION simulation_receipt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable: % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$;

CREATE TRIGGER simulation_stakes_immutable BEFORE UPDATE OR DELETE ON simulation_stakes FOR EACH ROW EXECUTE FUNCTION simulation_receipt_immutable();

CREATE TRIGGER simulation_commands_immutable BEFORE UPDATE OR DELETE ON simulation_commands FOR EACH ROW EXECUTE FUNCTION simulation_receipt_immutable();

CREATE FUNCTION simulation_stake_funded() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_balance numeric; wallet uuid;
        BEGIN
            SELECT id, balance INTO wallet, current_balance FROM play_wallets
            WHERE tenant_id = NEW.tenant_id AND user_id = NEW.player_id FOR UPDATE;
            IF NOT EXISTS (
                SELECT 1 FROM play_wallet_entries
                WHERE tenant_id = NEW.tenant_id AND wallet_id = wallet
                    AND kind = 'stake' AND delta = -NEW.amount AND bet_id = NEW.id
                    AND idempotency_key = 'simulation:stake:' || NEW.id::text
                    AND xmin::text::bigint = txid_current() % 4294967296
            ) OR current_balance IS DISTINCT FROM (
                SELECT coalesce(sum(delta), 0) FROM play_wallet_entries
                WHERE tenant_id = NEW.tenant_id AND wallet_id = wallet
            ) THEN
                RAISE EXCEPTION 'Simulation stake requires an atomic Arena Points ledger debit'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;

CREATE CONSTRAINT TRIGGER simulation_stakes_funded AFTER INSERT ON simulation_stakes DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION simulation_stake_funded();

ALTER TABLE roles NO FORCE ROW LEVEL SECURITY;

ALTER TABLE role_permissions NO FORCE ROW LEVEL SECURITY;

INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, 'simulations.manage' FROM roles WHERE name = 'tenant_admin' AND is_system
        UNION ALL
        SELECT tenant_id, id, 'simulations.control' FROM roles WHERE name IN ('tenant_admin', 'declarator') AND is_system
        ON CONFLICT DO NOTHING;

ALTER TABLE roles FORCE ROW LEVEL SECURITY;

ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0014' WHERE alembic_version.version_num = '0013';

COMMIT;

