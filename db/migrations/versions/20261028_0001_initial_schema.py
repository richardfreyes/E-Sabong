"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-28 07:47:11.451514+00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("default_enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_flags")),
        sa.UniqueConstraint("key", name=op.f("uq_feature_flags_key")),
    )
    op.create_table(
        "platform_users",
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "super_admin",
                "admin",
                "billing",
                "support",
                name="platformrole",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "suspended", name="accountstatus", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_users")),
    )
    op.create_index("uq_platform_users_email", "platform_users", [sa.literal_column("lower(email)")], unique=True)
    op.create_table(
        "subscription_plans",
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("saas", "maintenance", name="plankind", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("price_monthly_cents", sa.BigInteger(), nullable=False),
        sa.Column("price_annual_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("trial_days", sa.Integer(), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("modules", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price_monthly_cents >= 0 AND price_annual_cents >= 0", name=op.f("ck_subscription_plans_prices")),
        sa.CheckConstraint("trial_days >= 0", name=op.f("ck_subscription_plans_trial_days")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_plans")),
        sa.UniqueConstraint("code", name=op.f("uq_subscription_plans_code")),
    )
    op.create_table(
        "tenants",
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "suspended",
                "cancelled",
                name="tenantstatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "deployment_mode",
            sa.Enum("shared", "dedicated", name="deploymentmode", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("suspended_reason", sa.String(length=500), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("slug ~ '^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$'", name=op.f("ck_tenants_slug_format")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_index("uq_tenants_slug_live", "tenants", ["slug"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "audit_logs",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "actor_type",
            sa.Enum(
                "platform_user", "tenant_user", "system", name="actortype", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_label", sa.String(length=254), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_audit_logs_tenant_id_tenants"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_actor", "audit_logs", ["actor_type", "actor_id"], unique=False)
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"], unique=False)
    op.create_index("ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"], unique=False)
    op.create_table(
        "auth_sessions",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "subject_type",
            sa.Enum("platform", "tenant", name="subjecttype", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_auth_sessions_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index("ix_auth_sessions_family", "auth_sessions", ["family_id"], unique=False)
    op.create_index("ix_auth_sessions_subject", "auth_sessions", ["subject_type", "subject_id"], unique=False)
    op.create_index(op.f("ix_auth_sessions_tenant_id"), "auth_sessions", ["tenant_id"], unique=False)
    op.create_table(
        "events",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("venue", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "scheduled",
                "live",
                "completed",
                "cancelled",
                name="eventstatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name=op.f("ck_events_ends_after_starts")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_events_tenant_id_tenants"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_events_tenant_id_id"),
    )
    op.create_index(op.f("ix_events_tenant_id"), "events", ["tenant_id"], unique=False)
    op.create_index("ix_events_tenant_status_starts", "events", ["tenant_id", "status", "starts_at"], unique=False)
    op.create_table(
        "license_keys",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "license_type",
            sa.Enum("perpetual", "term", name="licensetype", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "issued",
                "active",
                "expired",
                "revoked",
                name="licensestatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("licensee_name", sa.String(length=200), nullable=False),
        sa.Column("max_activations", sa.Integer(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maintenance_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agreement_reference", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("issued_by_id", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=300), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["issued_by_id"], ["platform_users.id"], name=op.f("fk_license_keys_issued_by_id_platform_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_license_keys_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_license_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_license_keys_key_hash")),
    )
    op.create_index(op.f("ix_license_keys_tenant_id"), "license_keys", ["tenant_id"], unique=False)
    op.create_table(
        "roles",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_roles_tenant_id_tenants"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index(op.f("ix_roles_tenant_id"), "roles", ["tenant_id"], unique=False)
    op.create_table(
        "signup_requests",
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=False),
        sa.Column("contact_email", sa.String(length=254), nullable=False),
        sa.Column("contact_phone", sa.String(length=40), nullable=True),
        sa.Column("desired_slug", sa.String(length=63), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("billing_cycle", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="signupstatus", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name=op.f("fk_signup_requests_plan_id_subscription_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["platform_users.id"],
            name=op.f("fk_signup_requests_reviewed_by_platform_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_signup_requests_tenant_id_tenants"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signup_requests")),
    )
    op.create_index("ix_signup_requests_status_created", "signup_requests", ["status", "created_at"], unique=False)
    op.create_table(
        "tenant_branding",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("app_name", sa.String(length=120), nullable=False),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.Column("favicon_url", sa.String(length=500), nullable=True),
        sa.Column("primary_color", sa.String(length=7), nullable=False),
        sa.Column("secondary_color", sa.String(length=7), nullable=False),
        sa.Column("background_color", sa.String(length=7), nullable=False),
        sa.Column("surface_color", sa.String(length=7), nullable=False),
        sa.Column("login_title", sa.String(length=160), nullable=True),
        sa.Column("login_subtitle", sa.String(length=300), nullable=True),
        sa.Column("login_background_url", sa.String(length=500), nullable=True),
        sa.Column("contact_email", sa.String(length=254), nullable=True),
        sa.Column("contact_phone", sa.String(length=40), nullable=True),
        sa.Column("contact_address", sa.String(length=300), nullable=True),
        sa.Column("footer_text", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("background_color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_tenant_branding_background_color_hex")),
        sa.CheckConstraint("primary_color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_tenant_branding_primary_color_hex")),
        sa.CheckConstraint("secondary_color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_tenant_branding_secondary_color_hex")),
        sa.CheckConstraint("surface_color ~ '^#[0-9a-fA-F]{6}$'", name=op.f("ck_tenant_branding_surface_color_hex")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_branding_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_tenant_branding")),
    )
    op.create_table(
        "tenant_domains",
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("subdomain", "custom", name="domainkind", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column(
            "verification_status",
            sa.Enum(
                "pending", "verified", "failed", name="domainverification", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("verification_token", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_domains_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_domains")),
    )
    op.create_index(op.f("ix_tenant_domains_tenant_id"), "tenant_domains", ["tenant_id"], unique=False)
    op.create_index("uq_tenant_domains_domain", "tenant_domains", [sa.literal_column("lower(domain)")], unique=True)
    op.create_index(
        "uq_tenant_domains_primary", "tenant_domains", ["tenant_id"], unique=True, postgresql_where=sa.text("is_primary")
    )
    op.create_table(
        "tenant_feature_flags",
        sa.Column("flag_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["flag_id"], ["feature_flags.id"], name=op.f("fk_tenant_feature_flags_flag_id_feature_flags"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_feature_flags_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_feature_flags")),
    )
    op.create_index(op.f("ix_tenant_feature_flags_tenant_id"), "tenant_feature_flags", ["tenant_id"], unique=False)
    op.create_index("uq_tenant_feature_flags", "tenant_feature_flags", ["tenant_id", "flag_id"], unique=True)
    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("allow_self_registration", sa.Boolean(), nullable=False),
        sa.Column("enabled_modules", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("suspension_grace_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_settings_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_tenant_settings")),
    )
    op.create_table(
        "tenant_subscriptions",
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column(
            "billing_cycle",
            sa.Enum("monthly", "annual", name="billingcycle", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "trialing",
                "active",
                "past_due",
                "expired",
                "cancelled",
                name="subscriptionstatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("pending_plan_id", sa.UUID(), nullable=True),
        sa.Column(
            "pending_billing_cycle",
            sa.Enum("monthly", "annual", name="pending_billing_cycle", native_enum=False, create_constraint=True, length=32),
            nullable=True,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pending_plan_id"],
            ["subscription_plans.id"],
            name=op.f("fk_tenant_subscriptions_pending_plan_id_subscription_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name=op.f("fk_tenant_subscriptions_plan_id_subscription_plans"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_subscriptions_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_subscriptions")),
    )
    op.create_index("ix_tenant_subscriptions_period_end", "tenant_subscriptions", ["status", "current_period_end"], unique=False)
    op.create_index(op.f("ix_tenant_subscriptions_tenant_id"), "tenant_subscriptions", ["tenant_id"], unique=False)
    op.create_index(
        "uq_tenant_subscriptions_live",
        "tenant_subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('trialing', 'active', 'past_due')"),
    )
    op.create_table(
        "tenant_usage",
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_usage_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_usage")),
    )
    op.create_index(op.f("ix_tenant_usage_tenant_id"), "tenant_usage", ["tenant_id"], unique=False)
    op.create_index("uq_tenant_usage_metric_day", "tenant_usage", ["tenant_id", "metric", "period_date"], unique=True)
    op.create_table(
        "tenant_users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column(
            "account_type",
            sa.Enum(
                "admin",
                "operator",
                "master_agent",
                "agent",
                "player",
                name="accounttype",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "suspended", name="accountstatus", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_tenant_users_parent_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_users_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_users")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_users_tenant_id_id"),
    )
    op.create_index("ix_tenant_users_parent", "tenant_users", ["tenant_id", "parent_id"], unique=False)
    op.create_index(op.f("ix_tenant_users_tenant_id"), "tenant_users", ["tenant_id"], unique=False)
    op.create_index("ix_tenant_users_type", "tenant_users", ["tenant_id", "account_type"], unique=False)
    op.create_index(
        "uq_tenant_users_email",
        "tenant_users",
        ["tenant_id", sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND email IS NOT NULL"),
    )
    op.create_index(
        "uq_tenant_users_username",
        "tenant_users",
        ["tenant_id", sa.literal_column("lower(username)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "deployment_instances",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "provisioning",
                "active",
                "suspended",
                "decommissioned",
                name="deploymentstatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("license_id", sa.UUID(), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["license_id"], ["license_keys.id"], name=op.f("fk_deployment_instances_license_id_license_keys"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_deployment_instances_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_instances")),
        sa.UniqueConstraint("hostname", name=op.f("uq_deployment_instances_hostname")),
    )
    op.create_index("ix_deployment_instances_tenant", "deployment_instances", ["tenant_id"], unique=False)
    op.create_table(
        "invoices",
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "open", "paid", "void", name="invoicestatus", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal_cents", sa.BigInteger(), nullable=False),
        sa.Column("tax_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_reference", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("total_cents = subtotal_cents + tax_cents", name=op.f("ck_invoices_total_consistent")),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["tenant_subscriptions.id"],
            name=op.f("fk_invoices_subscription_id_tenant_subscriptions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_invoices_tenant_id_tenants"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("number", name=op.f("uq_invoices_number")),
    )
    op.create_index("ix_invoices_status_due", "invoices", ["status", "due_at"], unique=False)
    op.create_index(op.f("ix_invoices_tenant_id"), "invoices", ["tenant_id"], unique=False)
    op.create_index("ix_invoices_tenant_issued", "invoices", ["tenant_id", "issued_at"], unique=False)
    op.create_table(
        "license_activations",
        sa.Column("license_id", sa.UUID(), nullable=False),
        sa.Column("instance_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["license_id"], ["license_keys.id"], name=op.f("fk_license_activations_license_id_license_keys"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_license_activations")),
        sa.UniqueConstraint("license_id", "instance_fingerprint", name="uq_license_activation"),
    )
    op.create_index(op.f("ix_license_activations_license_id"), "license_activations", ["license_id"], unique=False)
    op.create_table(
        "matches",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("match_number", sa.Integer(), nullable=False),
        sa.Column("meron_name", sa.String(length=120), nullable=False),
        sa.Column("meron_details", sa.String(length=500), nullable=True),
        sa.Column("wala_name", sa.String(length=120), nullable=False),
        sa.Column("wala_details", sa.String(length=500), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "scheduled",
                "in_progress",
                "completed",
                "cancelled",
                name="matchstatus",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("cancel_reason", sa.String(length=300), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("match_number > 0", name=op.f("ck_matches_match_number_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"], ["events.tenant_id", "events.id"], name="fk_matches_event_same_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_matches_tenant_id_tenants"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matches")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_matches_tenant_id_id"),
    )
    op.create_index(op.f("ix_matches_tenant_id"), "matches", ["tenant_id"], unique=False)
    op.create_index("ix_matches_tenant_status", "matches", ["tenant_id", "status"], unique=False)
    op.create_index(
        "uq_matches_event_number",
        "matches",
        ["event_id", "match_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_notifications_user_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_notifications_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_tenant_id"), "notifications", ["tenant_id"], unique=False)
    op.create_index(
        "ix_notifications_user_unread", "notifications", ["tenant_id", "user_id", "read_at", "created_at"], unique=False
    )
    op.create_table(
        "point_accounts",
        sa.Column(
            "kind",
            sa.Enum("issuance", "user", name="accountkind", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'issuance' AND user_id IS NULL) OR (kind = 'user' AND user_id IS NOT NULL)",
            name=op.f("ck_point_accounts_owner_matches_kind"),
        ),
        sa.CheckConstraint("kind = 'issuance' OR balance >= 0", name=op.f("ck_point_accounts_non_negative_user_balance")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_point_accounts_user_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_point_accounts_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_accounts")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_point_accounts_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_point_accounts_tenant_user"),
    )
    op.create_index(op.f("ix_point_accounts_tenant_id"), "point_accounts", ["tenant_id"], unique=False)
    op.create_index(
        "uq_point_accounts_issuance", "point_accounts", ["tenant_id"], unique=True, postgresql_where=sa.text("kind = 'issuance'")
    )
    op.create_table(
        "role_permissions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_role_permissions_role_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_role_permissions_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission", name=op.f("pk_role_permissions")),
    )
    op.create_index(op.f("ix_role_permissions_tenant_id"), "role_permissions", ["tenant_id"], unique=False)
    op.create_table(
        "user_roles",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"], ["roles.tenant_id", "roles.id"], name="fk_user_roles_role_same_tenant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_user_roles_user_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=op.f("fk_user_roles_tenant_id_tenants"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id", name=op.f("pk_user_roles")),
    )
    op.create_index("ix_user_roles_role", "user_roles", ["role_id"], unique=False)
    op.create_index(op.f("ix_user_roles_tenant_id"), "user_roles", ["tenant_id"], unique=False)
    op.create_table(
        "invoice_lines",
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("plan", "proration", "credit", name="invoicelinekind", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_invoice_lines_invoice_id_invoices"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_invoice_lines_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_lines")),
    )
    op.create_index(op.f("ix_invoice_lines_invoice_id"), "invoice_lines", ["invoice_id"], unique=False)
    op.create_index(op.f("ix_invoice_lines_tenant_id"), "invoice_lines", ["tenant_id"], unique=False)
    op.create_table(
        "match_result_history",
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "meron", "wala", "draw", "no_contest", name="matchoutcome", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "previous_outcome",
            sa.Enum(
                "meron",
                "wala",
                "draw",
                "no_contest",
                name="previous_outcome",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("changed_by_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["matches.tenant_id", "matches.id"],
            name="fk_match_result_history_match_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_match_result_history_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_result_history")),
    )
    op.create_index("ix_match_result_history_match", "match_result_history", ["match_id", "revision"], unique=False)
    op.create_index(op.f("ix_match_result_history_tenant_id"), "match_result_history", ["tenant_id"], unique=False)
    op.create_table(
        "match_results",
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "meron", "wala", "draw", "no_contest", name="matchoutcome", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("declared_by_id", sa.UUID(), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["matches.tenant_id", "matches.id"],
            name="fk_match_results_match_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_match_results_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_results")),
        sa.UniqueConstraint("match_id", name="uq_match_results_match"),
    )
    op.create_index(op.f("ix_match_results_tenant_id"), "match_results", ["tenant_id"], unique=False)
    op.create_table(
        "point_transactions",
        sa.Column(
            "kind",
            sa.Enum(
                "allocation",
                "transfer",
                "adjustment",
                name="transactionkind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("from_account_id", sa.UUID(), nullable=False),
        sa.Column("to_account_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("memo", sa.String(length=300), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_point_transactions_amount_positive")),
        sa.CheckConstraint("from_account_id <> to_account_id", name=op.f("ck_point_transactions_distinct_accounts")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "from_account_id"],
            ["point_accounts.tenant_id", "point_accounts.id"],
            name="fk_point_transactions_from_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "to_account_id"],
            ["point_accounts.tenant_id", "point_accounts.id"],
            name="fk_point_transactions_to_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_point_transactions_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_transactions")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_point_transactions_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_point_transactions_idempotency"),
    )
    op.create_index("ix_point_transactions_from", "point_transactions", ["from_account_id", "created_at"], unique=False)
    op.create_index("ix_point_transactions_tenant_created", "point_transactions", ["tenant_id", "created_at"], unique=False)
    op.create_index(op.f("ix_point_transactions_tenant_id"), "point_transactions", ["tenant_id"], unique=False)
    op.create_index("ix_point_transactions_to", "point_transactions", ["to_account_id", "created_at"], unique=False)
    op.create_table(
        "point_ledger_entries",
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("delta <> 0", name=op.f("ck_point_ledger_entries_delta_non_zero")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["point_accounts.tenant_id", "point_accounts.id"],
            name="fk_point_ledger_entries_account_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "transaction_id"],
            ["point_transactions.tenant_id", "point_transactions.id"],
            name="fk_point_ledger_entries_tx_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_point_ledger_entries_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_ledger_entries")),
    )
    op.create_index("ix_point_ledger_entries_account_created", "point_ledger_entries", ["account_id", "created_at"], unique=False)
    op.create_index(op.f("ix_point_ledger_entries_tenant_id"), "point_ledger_entries", ["tenant_id"], unique=False)
    op.create_index("ix_point_ledger_entries_tx", "point_ledger_entries", ["transaction_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_point_ledger_entries_tx", table_name="point_ledger_entries")
    op.drop_index(op.f("ix_point_ledger_entries_tenant_id"), table_name="point_ledger_entries")
    op.drop_index("ix_point_ledger_entries_account_created", table_name="point_ledger_entries")
    op.drop_table("point_ledger_entries")
    op.drop_index("ix_point_transactions_to", table_name="point_transactions")
    op.drop_index(op.f("ix_point_transactions_tenant_id"), table_name="point_transactions")
    op.drop_index("ix_point_transactions_tenant_created", table_name="point_transactions")
    op.drop_index("ix_point_transactions_from", table_name="point_transactions")
    op.drop_table("point_transactions")
    op.drop_index(op.f("ix_match_results_tenant_id"), table_name="match_results")
    op.drop_table("match_results")
    op.drop_index(op.f("ix_match_result_history_tenant_id"), table_name="match_result_history")
    op.drop_index("ix_match_result_history_match", table_name="match_result_history")
    op.drop_table("match_result_history")
    op.drop_index(op.f("ix_invoice_lines_tenant_id"), table_name="invoice_lines")
    op.drop_index(op.f("ix_invoice_lines_invoice_id"), table_name="invoice_lines")
    op.drop_table("invoice_lines")
    op.drop_index(op.f("ix_user_roles_tenant_id"), table_name="user_roles")
    op.drop_index("ix_user_roles_role", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index(op.f("ix_role_permissions_tenant_id"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("uq_point_accounts_issuance", table_name="point_accounts", postgresql_where=sa.text("kind = 'issuance'"))
    op.drop_index(op.f("ix_point_accounts_tenant_id"), table_name="point_accounts")
    op.drop_table("point_accounts")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index(op.f("ix_notifications_tenant_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("uq_matches_event_number", table_name="matches", postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_index("ix_matches_tenant_status", table_name="matches")
    op.drop_index(op.f("ix_matches_tenant_id"), table_name="matches")
    op.drop_table("matches")
    op.drop_index(op.f("ix_license_activations_license_id"), table_name="license_activations")
    op.drop_table("license_activations")
    op.drop_index("ix_invoices_tenant_issued", table_name="invoices")
    op.drop_index(op.f("ix_invoices_tenant_id"), table_name="invoices")
    op.drop_index("ix_invoices_status_due", table_name="invoices")
    op.drop_table("invoices")
    op.drop_index("ix_deployment_instances_tenant", table_name="deployment_instances")
    op.drop_table("deployment_instances")
    op.drop_index("uq_tenant_users_username", table_name="tenant_users", postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_index(
        "uq_tenant_users_email", table_name="tenant_users", postgresql_where=sa.text("deleted_at IS NULL AND email IS NOT NULL")
    )
    op.drop_index("ix_tenant_users_type", table_name="tenant_users")
    op.drop_index(op.f("ix_tenant_users_tenant_id"), table_name="tenant_users")
    op.drop_index("ix_tenant_users_parent", table_name="tenant_users")
    op.drop_table("tenant_users")
    op.drop_index("uq_tenant_usage_metric_day", table_name="tenant_usage")
    op.drop_index(op.f("ix_tenant_usage_tenant_id"), table_name="tenant_usage")
    op.drop_table("tenant_usage")
    op.drop_index(
        "uq_tenant_subscriptions_live",
        table_name="tenant_subscriptions",
        postgresql_where=sa.text("status IN ('trialing', 'active', 'past_due')"),
    )
    op.drop_index(op.f("ix_tenant_subscriptions_tenant_id"), table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscriptions_period_end", table_name="tenant_subscriptions")
    op.drop_table("tenant_subscriptions")
    op.drop_table("tenant_settings")
    op.drop_index("uq_tenant_feature_flags", table_name="tenant_feature_flags")
    op.drop_index(op.f("ix_tenant_feature_flags_tenant_id"), table_name="tenant_feature_flags")
    op.drop_table("tenant_feature_flags")
    op.drop_index("uq_tenant_domains_primary", table_name="tenant_domains", postgresql_where=sa.text("is_primary"))
    op.drop_index("uq_tenant_domains_domain", table_name="tenant_domains")
    op.drop_index(op.f("ix_tenant_domains_tenant_id"), table_name="tenant_domains")
    op.drop_table("tenant_domains")
    op.drop_table("tenant_branding")
    op.drop_index("ix_signup_requests_status_created", table_name="signup_requests")
    op.drop_table("signup_requests")
    op.drop_index(op.f("ix_roles_tenant_id"), table_name="roles")
    op.drop_table("roles")
    op.drop_index(op.f("ix_license_keys_tenant_id"), table_name="license_keys")
    op.drop_table("license_keys")
    op.drop_index("ix_events_tenant_status_starts", table_name="events")
    op.drop_index(op.f("ix_events_tenant_id"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_auth_sessions_tenant_id"), table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_subject", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_family", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_audit_logs_tenant_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("uq_tenants_slug_live", table_name="tenants", postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_table("tenants")
    op.drop_table("subscription_plans")
    op.drop_index("uq_platform_users_email", table_name="platform_users")
    op.drop_table("platform_users")
    op.drop_table("feature_flags")
