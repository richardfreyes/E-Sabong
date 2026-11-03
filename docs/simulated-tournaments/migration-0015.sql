BEGIN;

-- Running upgrade 0014 -> 0015

CREATE TABLE simulation_settlements (
    match_id UUID NOT NULL,
    outcome VARCHAR(32) NOT NULL,
    total_staked NUMERIC(16, 2) NOT NULL,
    fee_amount NUMERIC(16, 2) NOT NULL,
    distributed NUMERIC(16, 2) NOT NULL,
    refunded BOOLEAN NOT NULL,
    lines JSONB NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_settlements PRIMARY KEY (id),
    CONSTRAINT uq_simulation_settlements_match UNIQUE (tenant_id, match_id),
    CONSTRAINT fk_simulation_settlements_match FOREIGN KEY(tenant_id, match_id) REFERENCES simulation_matches (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_simulation_settlements_amounts_non_negative CHECK (total_staked >= 0 AND fee_amount >= 0 AND distributed >= 0),
    CONSTRAINT ck_simulation_settlements_conservation CHECK (total_staked = fee_amount + distributed),
    CONSTRAINT ck_simulation_settlements_simulationoutcome CHECK (outcome IN ('team_a', 'team_b', 'draw', 'cancelled')),
    CONSTRAINT fk_simulation_settlements_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE INDEX ix_simulation_settlements_tenant_id ON simulation_settlements (tenant_id);

GRANT UPDATE (created_at) ON simulation_controllers TO esabong_app;

ALTER TABLE "simulation_settlements" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "simulation_settlements" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "simulation_settlements" USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT ON "simulation_settlements" TO esabong_app;

GRANT SELECT ON "simulation_settlements" TO esabong_platform;

CREATE TRIGGER simulation_settlements_immutable BEFORE UPDATE OR DELETE ON simulation_settlements FOR EACH ROW EXECUTE FUNCTION simulation_receipt_immutable();

CREATE UNIQUE INDEX uq_simulation_matches_active ON simulation_matches (tenant_id, arena_id) WHERE state NOT IN ('pending', 'settled');

CREATE FUNCTION simulation_state_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.state = OLD.state THEN RETURN NEW; END IF;
            IF NOT (
                (OLD.state = 'pending' AND NEW.state = 'wagering_open') OR
                (OLD.state = 'wagering_open' AND NEW.state IN ('final_call', 'wagering_closed')) OR
                (OLD.state = 'final_call' AND NEW.state IN ('wagering_open', 'wagering_closed')) OR
                (OLD.state = 'wagering_closed' AND NEW.state = 'simulation_running') OR
                (OLD.state = 'simulation_running' AND NEW.state = 'result_declared') OR
                (OLD.state = 'result_declared' AND NEW.state = 'settled')
            ) OR NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'Invalid simulation state transition' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

CREATE TRIGGER simulation_matches_transition BEFORE UPDATE OF state ON simulation_matches FOR EACH ROW EXECUTE FUNCTION simulation_state_transition();

CREATE FUNCTION simulation_wager_open() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE phase text; active boolean;
        BEGIN
            SELECT m.state, a.enabled INTO phase, active FROM simulation_matches m
            JOIN simulation_arenas a ON a.tenant_id = m.tenant_id AND a.id = m.arena_id
            WHERE m.tenant_id = NEW.tenant_id AND m.id = NEW.match_id FOR SHARE OF a, m;
            IF phase IS DISTINCT FROM 'wagering_open' OR active IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'Simulation wagering is closed' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

CREATE TRIGGER simulation_stakes_open BEFORE INSERT ON simulation_stakes FOR EACH ROW EXECUTE FUNCTION simulation_wager_open();

CREATE FUNCTION simulation_settled_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.state = 'settled' AND NOT EXISTS (
                SELECT 1 FROM simulation_settlements WHERE tenant_id = NEW.tenant_id AND match_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'Settled simulation requires a receipt' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;

CREATE CONSTRAINT TRIGGER simulation_matches_receipt AFTER UPDATE ON simulation_matches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION simulation_settled_receipt();

UPDATE alembic_version SET version_num='0015' WHERE alembic_version.version_num = '0014';

COMMIT;

