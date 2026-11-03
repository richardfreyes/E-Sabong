"""Neutral pool arithmetic in Decimal; never creates currency or host commissions."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from uuid import UUID

from app.modules.simulations.models import SimulationOutcome, SimulationSide

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Line:
    id: UUID
    side: SimulationSide
    amount: Decimal


@dataclass(frozen=True)
class Allocation:
    payouts: dict[UUID, Decimal]
    total: Decimal
    fee: Decimal
    refunded: bool


def odds(a: Decimal, b: Decimal, fee: Decimal) -> tuple[Decimal | None, Decimal | None]:
    if a <= 0 or b <= 0:
        return None, None
    net = a + b - ((a + b) * fee).quantize(CENT, rounding=ROUND_HALF_UP)
    return net / a, net / b


def allocate(lines: list[Line], outcome: SimulationOutcome, fee_rate: Decimal) -> Allocation:
    """Largest remainder allocation in caller-supplied stable order; exact conservation of cents."""
    if not fee_rate.is_finite() or not ZERO <= fee_rate < Decimal("0.5"):
        raise ValueError("Invalid simulation fee")
    a = sum((line.amount for line in lines if line.side == SimulationSide.TEAM_A), ZERO)
    b = sum((line.amount for line in lines if line.side == SimulationSide.TEAM_B), ZERO)
    total = a + b
    if outcome in (SimulationOutcome.DRAW, SimulationOutcome.CANCELLED) or not a or not b:
        return Allocation({line.id: line.amount for line in lines}, total, ZERO, True)
    winning_side = SimulationSide(outcome.value)
    winners = [line for line in lines if line.side == winning_side]
    fee = (total * fee_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    net = total - fee
    winning_pool = sum((line.amount for line in winners), ZERO)
    # Integer arithmetic avoids Decimal context precision changing a remainder ordering.
    net_cents = int(net / CENT)
    weights = [int(line.amount / CENT) for line in winners]
    pool_cents = int(winning_pool / CENT)
    cents = [net_cents * weight // pool_cents for weight in weights]
    remainders = [net_cents * weight % pool_cents for weight in weights]
    order = sorted(range(len(winners)), key=lambda i: (-remainders[i], i))
    for i in order[: net_cents - sum(cents)]:
        cents[i] += 1
    payouts = dict.fromkeys((line.id for line in lines), ZERO)
    for line, amount in zip(winners, cents, strict=True):
        payouts[line.id] = (Decimal(amount) * CENT).quantize(CENT, rounding=ROUND_DOWN)
    return Allocation(payouts, total, fee, False)
