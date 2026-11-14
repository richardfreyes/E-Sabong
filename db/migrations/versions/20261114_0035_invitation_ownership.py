"""Invitations belong to the account that created them.

Existing agent, sub-agent, and player accounts gain one accepted invitation. The inviter is
the agent who created the account. A referral sign-up (no creator) stays with the referring
account. Parent changes, commissions, and network membership are not rewritten.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

from app.core.migration_helpers import disable_tenant_rls, enable_tenant_rls, grant, revoke_all
from app.modules.agents.invitations import BACKFILL_SQL

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE account_invitations (
            id UUID DEFAULT gen_random_uuid() NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
            tenant_id UUID NOT NULL,
            invited_by_user_id UUID NOT NULL,
            invitee_user_id UUID,
            invitee_username VARCHAR(64),
            invitee_full_name VARCHAR(200),
            account_type VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            CONSTRAINT pk_account_invitations PRIMARY KEY (id),
            CONSTRAINT uq_account_invitations_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT ck_account_invitations_invitationrole CHECK (account_type IN ('agent', 'sub_agent', 'player')),
            CONSTRAINT ck_account_invitations_invitationstatus CHECK (status IN ('pending', 'accepted', 'revoked')),
            CONSTRAINT ck_account_invitations_acceptance CHECK ((status = 'accepted') = (invitee_user_id IS NOT NULL)),
            CONSTRAINT fk_account_invitations_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
            CONSTRAINT fk_account_invitations_invited_by FOREIGN KEY (tenant_id, invited_by_user_id)
                REFERENCES tenant_users (tenant_id, id),
            CONSTRAINT fk_account_invitations_invitee FOREIGN KEY (tenant_id, invitee_user_id)
                REFERENCES tenant_users (tenant_id, id)
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX uq_account_invitations_invitee ON account_invitations (tenant_id, invitee_user_id) "
        "WHERE invitee_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_account_invitations_owner ON account_invitations (tenant_id, invited_by_user_id, created_at)"
    )
    grant("account_invitations", app="SELECT, INSERT, UPDATE", platform="SELECT, INSERT, UPDATE")
    # The migration role must read every tenant's existing accounts. RLS stays enabled for the app.
    op.execute("ALTER TABLE tenant_users NO FORCE ROW LEVEL SECURITY")
    op.execute(BACKFILL_SQL)
    op.execute("ALTER TABLE tenant_users FORCE ROW LEVEL SECURITY")
    enable_tenant_rls("account_invitations")


def downgrade() -> None:
    disable_tenant_rls("account_invitations")
    revoke_all("account_invitations")
    op.execute("DROP TABLE account_invitations")
