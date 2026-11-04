from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.simulations.math import Line, allocate, odds
from app.modules.simulations.models import SimulationOutcome as O
from app.modules.simulations.models import SimulationSide as S

D = Decimal


def test_proportional_shares_fee_and_deterministic_cent_rounding():
    lines = [Line(UUID(int=i), S.TEAM_A, D("0.01")) for i in range(1, 4)] + [Line(UUID(int=4), S.TEAM_B, D("0.02"))]
    result = allocate(lines, O.TEAM_A, D("0.05"))
    assert list(result.payouts.values()) == [D("0.02"), D("0.02"), D("0.01"), D("0.00")]
    assert sum(result.payouts.values()) + result.fee == result.total
    lines = [Line(UUID(int=1), S.TEAM_A, D("100.00")), Line(UUID(int=2), S.TEAM_B, D("166.00"))]
    result = allocate(lines, O.TEAM_A, D("0.10"))
    assert result.payouts[UUID(int=1)] == D("239.40") and result.fee == D("26.60")
    assert odds(D("100.00"), D("166.00"), D("0.10"))[0] == D("2.394")


@pytest.mark.parametrize("outcome", [O.DRAW, O.CANCELLED, O.TEAM_A, O.TEAM_B])
def test_refunds_when_draw_cancelled_or_a_side_is_empty(outcome):
    lines = [Line(UUID(int=1), S.TEAM_A, D("123.45"))]
    result = allocate(lines, outcome, D("0.05"))
    assert result.refunded and result.fee == 0 and result.payouts[UUID(int=1)] == D("123.45")


@pytest.mark.parametrize("rate", ["NaN", "Infinity", "-0.01", "0.5"])
def test_invalid_fee_is_rejected(rate):
    with pytest.raises(ValueError):
        allocate([], O.DRAW, D(rate))
