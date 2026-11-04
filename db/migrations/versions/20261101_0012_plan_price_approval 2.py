"""Explicit price approval for subscription plans

Adds ``prices_approved_at`` / ``prices_approved_by`` to ``subscription_plans``. A plan can be
checked out (new subscription, plan change, signup request) only when it is active, priced in
the platform currency (PHP) and its prices were approved by a platform user.

Existing rows are left with NULL approval on purpose: seeded catalogue prices (e.g. the
USD-era 4900/14900/49900 centavo figures) must not be treated as approved. Owners review and
approve them in the console. No price, currency or subscription row is changed; existing
subscriptions keep renewing at their recorded prices.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("subscription_plans", sa.Column("prices_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscription_plans", sa.Column("prices_approved_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_subscription_plans_prices_approved_by_platform_users"),
        "subscription_plans",
        "platform_users",
        ["prices_approved_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_subscription_plans_prices_approved_by_platform_users"), "subscription_plans", type_="foreignkey")
    op.drop_column("subscription_plans", "prices_approved_by")
    op.drop_column("subscription_plans", "prices_approved_at")
