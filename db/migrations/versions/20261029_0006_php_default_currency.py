"""PHP as the default currency for new billing records

Only affects rows inserted from now on. Existing plans and invoices keep their
original amounts and currency codes; converting historical USD records would
need a separate migration with an approved exchange rate and conversion date.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-29 10:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("subscription_plans", "currency", server_default="PHP")
    # Format check only, so legacy USD rows remain valid.
    op.create_check_constraint("ck_subscription_plans_currency_code", "subscription_plans", "currency ~ '^[A-Z]{3}$'")
    op.create_check_constraint("ck_invoices_currency_code", "invoices", "currency ~ '^[A-Z]{3}$'")


def downgrade() -> None:
    op.drop_constraint("ck_invoices_currency_code", "invoices", type_="check")
    op.drop_constraint("ck_subscription_plans_currency_code", "subscription_plans", type_="check")
    op.alter_column("subscription_plans", "currency", server_default=None)
