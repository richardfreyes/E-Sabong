"""row-level security, role grants, append-only tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28 08:00:00+00:00
"""

from collections.abc import Sequence

from app.core.migration_helpers import (
    create_current_tenant_function,
    disable_tenant_rls,
    drop_current_tenant_function,
    enable_tenant_rls,
    grant,
    revoke_all,
)

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FULL = "SELECT, INSERT, UPDATE, DELETE"
NO_DELETE = "SELECT, INSERT, UPDATE"
APPEND_ONLY = "SELECT, INSERT"
READ = "SELECT"

# table -> (app role privileges or None, platform role privileges)
PRIVILEGES: dict[str, tuple[str | None, str]] = {
    # global catalogues, no RLS
    "subscription_plans": (READ, FULL),
    "feature_flags": (READ, FULL),
    # platform-only: the tenant-facing role has no access at all
    "platform_users": (None, NO_DELETE),
    "signup_requests": (None, NO_DELETE),
    "license_keys": (None, NO_DELETE),
    "license_activations": (None, NO_DELETE),
    "deployment_instances": (None, NO_DELETE),
    # tenant-owned, RLS enforced
    "tenants": (READ, NO_DELETE),
    "tenant_domains": (FULL, FULL),
    "tenant_branding": ("SELECT, UPDATE", NO_DELETE),
    "tenant_settings": ("SELECT, UPDATE", NO_DELETE),
    "tenant_feature_flags": (READ, FULL),
    "tenant_usage": (READ, NO_DELETE),
    "tenant_subscriptions": (READ, NO_DELETE),
    "invoices": (READ, NO_DELETE),
    "invoice_lines": (READ, FULL),
    "tenant_users": (NO_DELETE, NO_DELETE),
    "roles": (FULL, FULL),
    "role_permissions": (FULL, FULL),
    "user_roles": (FULL, FULL),
    "auth_sessions": (FULL, FULL),
    "events": (NO_DELETE, NO_DELETE),
    "matches": (NO_DELETE, NO_DELETE),
    "match_results": (NO_DELETE, NO_DELETE),
    "match_result_history": (APPEND_ONLY, APPEND_ONLY),
    "point_accounts": (NO_DELETE, NO_DELETE),
    "point_transactions": (APPEND_ONLY, APPEND_ONLY),
    "point_ledger_entries": (APPEND_ONLY, APPEND_ONLY),
    "notifications": (FULL, FULL),
    "audit_logs": (APPEND_ONLY, APPEND_ONLY),
}

RLS_TABLES = [
    t
    for t in PRIVILEGES
    if t
    not in {
        "subscription_plans",
        "feature_flags",
        "platform_users",
        "signup_requests",
        "license_keys",
        "license_activations",
        "deployment_instances",
        "tenants",
    }
]


def upgrade() -> None:
    create_current_tenant_function()
    enable_tenant_rls("tenants", column="id")
    for table in RLS_TABLES:
        enable_tenant_rls(table)
    for table, (app_privs, platform_privs) in PRIVILEGES.items():
        grant(table, app=app_privs, platform=platform_privs)


def downgrade() -> None:
    for table in PRIVILEGES:
        revoke_all(table)
    for table in RLS_TABLES:
        disable_tenant_rls(table)
    disable_tenant_rls("tenants")
    drop_current_tenant_function()
