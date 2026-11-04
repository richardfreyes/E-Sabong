"""Add isolated simulated tournament schema and least-privilege controller permissions.

Existing users, role IDs, balances and historical ledgers are preserved. No alternate
wallet is created. Tables are initially empty and the simulation engine remains disabled.
Downgrade refuses to discard any simulation records.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

from app.core.migration_helpers import enable_tenant_rls, grant

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLES = ("simulation_arenas", "simulation_controllers", "simulation_matches", "simulation_stakes", "simulation_commands")


def upgrade() -> None:
    op.execute("""
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
)
""")
    op.execute("CREATE INDEX ix_simulation_arenas_tenant_id ON simulation_arenas (tenant_id)")
    enable_tenant_rls("simulation_arenas")
    grant("simulation_arenas", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
CREATE TABLE simulation_controllers (
    arena_id UUID NOT NULL,
    user_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    tenant_id UUID NOT NULL,
    CONSTRAINT pk_simulation_controllers PRIMARY KEY (arena_id, user_id),
    CONSTRAINT fk_simulation_controllers_arena FOREIGN KEY(tenant_id, arena_id) REFERENCES simulation_arenas (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_simulation_controllers_user FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_simulation_controllers_tenant_id_tenants FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_simulation_controllers_tenant_id ON simulation_controllers (tenant_id)")
    enable_tenant_rls("simulation_controllers")
    grant("simulation_controllers", app="SELECT, INSERT, DELETE", platform="SELECT")
    op.execute("""
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
)
""")
    op.execute("CREATE INDEX ix_simulation_matches_tenant_id ON simulation_matches (tenant_id)")
    enable_tenant_rls("simulation_matches")
    grant("simulation_matches", app="SELECT, INSERT, UPDATE", platform="SELECT")
    op.execute("""
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
)
""")
    op.execute("CREATE INDEX ix_simulation_stakes_tenant_id ON simulation_stakes (tenant_id)")
    op.execute("CREATE INDEX ix_simulation_stakes_match ON simulation_stakes (tenant_id, match_id)")
    enable_tenant_rls("simulation_stakes")
    grant("simulation_stakes", app="SELECT, INSERT", platform="SELECT")
    op.execute("""
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
)
""")
    op.execute("CREATE INDEX ix_simulation_commands_tenant_id ON simulation_commands (tenant_id)")
    enable_tenant_rls("simulation_commands")
    grant("simulation_commands", app="SELECT, INSERT", platform="SELECT")
    op.execute("""
        CREATE FUNCTION simulation_receipt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable: % is not permitted', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$
    """)
    for table in ("simulation_stakes", "simulation_commands"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION simulation_receipt_immutable()"
        )
    # A future wagering handler must debit the existing wallet in this same transaction.
    # A stake row alone is not accepted, even from an accidental direct application insert.
    # The future handler posts in the outer transaction (ON CONFLICT, no savepoint).
    # xmin checks the posting transaction; ledger created_at uses clock_timestamp().
    op.execute("""
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
        END $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER simulation_stakes_funded AFTER INSERT ON simulation_stakes "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION simulation_stake_funded()"
    )
    # These tables have FORCE RLS; temporarily lift it only for the schema owner
    # for this transactional, additive backfill. Application RLS stays enabled.
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO role_permissions (tenant_id, role_id, permission)
        SELECT tenant_id, id, 'simulations.manage' FROM roles WHERE name = 'tenant_admin' AND is_system
        UNION ALL
        SELECT tenant_id, id, 'simulations.control' FROM roles WHERE name IN ('tenant_admin', 'declarator') AND is_system
        ON CONFLICT DO NOTHING
    """)
    for table in ("roles", "role_permissions"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Check every tenant before dropping tables; FORCE RLS would hide records from
    # the migration owner without lifting it. An exception rolls back the entire DDL.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM simulation_arenas)
                OR EXISTS (SELECT 1 FROM simulation_controllers)
                OR EXISTS (SELECT 1 FROM simulation_matches)
                OR EXISTS (SELECT 1 FROM simulation_stakes)
                OR EXISTS (SELECT 1 FROM simulation_commands) THEN
                RAISE EXCEPTION 'Refusing to discard simulation history; retain revision 0014 and roll back application code';
            END IF;
        END $$
    """)
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION simulation_receipt_immutable()")
    op.execute("DROP FUNCTION IF EXISTS simulation_stake_funded()")
    # Permission strings are inert without the simulation API. Preserve assignments
    # so application rollbacks never erase intentional role configuration.
