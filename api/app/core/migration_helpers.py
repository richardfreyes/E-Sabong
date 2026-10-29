"""Helpers used by Alembic revisions to keep RLS and grants consistent.

Every new tenant-owned table must call ``enable_tenant_rls`` and ``grant`` in the
migration that creates it; ``tests/test_isolation.py`` fails if a table carrying a
``tenant_id`` column is missing a policy.
"""

from alembic import op

APP_ROLE = "esabong_app"
PLATFORM_ROLE = "esabong_platform"

CURRENT_TENANT_FN = "app_current_tenant()"


def create_current_tenant_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )


def drop_current_tenant_function() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")


def enable_tenant_rls(table: str, column: str = "tenant_id") -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    # FORCE applies the policy to the table owner too, so only BYPASSRLS roles skip it.
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING ({column} = {CURRENT_TENANT_FN}) "
        f"WITH CHECK ({column} = {CURRENT_TENANT_FN})"
    )


def disable_tenant_rls(table: str) -> None:
    op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def grant(table: str, *, app: str | None, platform: str) -> None:
    if app:
        op.execute(f'GRANT {app} ON "{table}" TO {APP_ROLE}')
    op.execute(f'GRANT {platform} ON "{table}" TO {PLATFORM_ROLE}')


def revoke_all(table: str) -> None:
    op.execute(f'REVOKE ALL ON "{table}" FROM {APP_ROLE}, {PLATFORM_ROLE}')
