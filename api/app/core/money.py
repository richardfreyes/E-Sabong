"""Currency configuration and exact money arithmetic.

Amounts are integer minor units (centavos for PHP). Any multiplication by a
fraction goes through Decimal and rounds half-up once, so results never depend
on binary floating point. Mirror SUPPORTED_CURRENCIES in web/shared/pipes/currency.ts.
"""

from decimal import ROUND_HALF_UP, Decimal

# ISO 4217 code -> display name. Add a currency here (and in the web config) to support it.
SUPPORTED_CURRENCIES: dict[str, str] = {
    "PHP": "Philippine Peso",
}


def to_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def scale(cents: int, fraction: Decimal) -> int:
    """cents × fraction, rounded half-up to whole cents."""
    return to_cents(Decimal(cents) * fraction)


def basis_points(cents: int, bps: int) -> int:
    """Percentage of an amount expressed in basis points (1200 = 12%)."""
    return to_cents(Decimal(cents) * Decimal(bps) / Decimal(10_000))
