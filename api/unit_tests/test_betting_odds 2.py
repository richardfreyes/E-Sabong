"""Pool odds and settlement arithmetic (pure functions, no database)."""

from decimal import Decimal as D

import pytest

from app.modules.betting.odds import (
    BetLine,
    Side,
    Verdict,
    allocate,
    display_odds,
    estimated_pays,
    fixed_pays,
    obligations,
    pool_odds,
    settle,
)

RATE = D("0.10")


@pytest.mark.parametrize(
    ("meron", "wala", "shown_meron", "shown_wala"),
    [
        ("780261.90", "906433.56", "1.95", "1.67"),  # reference screenshot
        ("563516.93", "435332.34", "1.60", "2.07"),  # 2.06500703... rounds half up, never truncates
        ("869347.02", "828986.34", "1.76", "1.84"),
        ("183", "183", "1.80", "1.80"),
        ("200", "166", "1.65", "1.98"),
    ],
)
def test_displayed_odds_match_the_reference_values(meron, wala, shown_meron, shown_wala):
    odds = pool_odds(D(meron), D(wala), RATE)
    assert (display_odds(odds.meron), display_odds(odds.wala)) == (D(shown_meron), D(shown_wala))


def test_odds_stay_unrounded_internally():
    odds = pool_odds(D("780261.90"), D("906433.56"), RATE)
    distributable = D("1686695.46") * D("0.9")
    assert distributable == D("1518025.914")
    assert odds.meron == distributable / D("780261.90")
    assert odds.meron != display_odds(odds.meron)


def test_more_money_on_a_side_shortens_it_and_lengthens_the_other():
    even = pool_odds(D(183), D(183), RATE)
    tilted = pool_odds(D(200), D(166), RATE)
    assert tilted.meron < even.meron and tilted.wala > even.wala
    back = pool_odds(D(166), D(200), RATE)
    assert back.meron > even.meron and back.wala < even.wala


@pytest.mark.parametrize(("meron", "wala"), [(0, 0), (0, 500), (500, 0)])
def test_empty_side_means_no_odds_rather_than_a_division_by_zero(meron, wala):
    odds = pool_odds(D(meron), D(wala), RATE)
    assert odds.meron is None and odds.wala is None
    assert display_odds(odds.meron) is None


def test_draw_never_enters_the_two_sided_odds():
    # The function does not even take the DRAW pool; settlement keeps it separate too.
    lines = [BetLine(1, Side.MERON, D(183)), BetLine(2, Side.WALA, D(183)), BetLine(3, Side.DRAW, D(972), D(8))]
    s = settle(lines, "meron", RATE)
    assert s.distributable == D("329.40")  # (183 + 183) x 0.9, the 972 DRAW stake is not in it
    assert s.verdicts[3] == (Verdict.LOST, D("0.00"))


def test_estimated_pays_uses_unrounded_odds():
    odds = pool_odds(D("780261.90"), D("906433.56"), RATE)
    assert estimated_pays(D(100), odds.meron) == D("194.55")  # not 100 x 1.95 = 195.00
    assert estimated_pays(D(0), odds.meron) == D("0.00")
    assert estimated_pays(D(100), None) is None
    assert fixed_pays(D(50), D(8)) == D("400.00")


def test_allocation_sums_exactly_and_breaks_ties_by_order():
    shares = allocate(D("100.00"), [("a", D(1)), ("b", D(1)), ("c", D(1))])
    assert shares == {"a": D("33.34"), "b": D("33.33"), "c": D("33.33")}
    shares = allocate(D("10.00"), [("x", D("3.33")), ("y", D("6.67")), ("z", D("0.01"))])
    assert sum(shares.values()) == D("10.00")
    assert allocate(D("5.00"), []) == {}


def test_meron_win_splits_the_distributable_pool_pro_rata():
    bets = [
        BetLine("m1", Side.MERON, D(100)),
        BetLine("m2", Side.MERON, D(100)),
        BetLine("w1", Side.WALA, D(166)),
        BetLine("d1", Side.DRAW, D(50), D(8)),
    ]
    s = settle(bets, "meron", RATE)
    assert (s.meron_pool, s.wala_pool, s.draw_pool) == (D(200), D(166), D(50))
    assert s.commission == D("36.60") and s.distributable == D("329.40")
    assert s.verdicts["m1"] == (Verdict.WON, D("164.70")) and s.verdicts["m2"] == (Verdict.WON, D("164.70"))
    assert s.verdicts["w1"] == (Verdict.LOST, D("0.00")) and s.verdicts["d1"][0] == Verdict.LOST
    # Accounting reconciles to the cent: MERON/WALA stakes = winning payouts + commission.
    assert s.meron_pool + s.wala_pool == s.verdicts["m1"][1] + s.verdicts["m2"][1] + s.commission
    assert display_odds(s.meron_odds) == D("1.65") and display_odds(s.wala_odds) == D("1.98")


def test_uneven_winning_stakes_reconcile_to_the_cent():
    bets = [BetLine(i, Side.WALA, D(a)) for i, a in enumerate(["33.33", "33.33", "33.34", "0.01"])]
    bets.append(BetLine("m", Side.MERON, D("777.77")))
    s = settle(bets, "wala", D("0.0735"))
    won = sum(p for v, p in s.verdicts.values() if v == Verdict.WON)
    assert won == s.distributable
    assert s.commission + s.distributable == D("100.01") + D("777.77")


def test_one_sided_book_refunds_meron_and_wala():
    bets = [BetLine("m", Side.MERON, D(500)), BetLine("d", Side.DRAW, D(20), D(8))]
    s = settle(bets, "meron", RATE)
    assert s.verdicts["m"] == (Verdict.REFUNDED, D(500))
    assert s.verdicts["d"] == (Verdict.LOST, D("0.00"))
    assert s.commission == 0


def test_draw_pays_fixed_and_refunds_both_sides():
    bets = [
        BetLine("m", Side.MERON, D(100)),
        BetLine("w", Side.WALA, D(200)),
        BetLine("d1", Side.DRAW, D(50), D(8)),
        BetLine("d2", Side.DRAW, D(25), D("7.50")),  # accepted before the multiplier changed
    ]
    s = settle(bets, "draw", RATE)
    assert s.verdicts["d1"] == (Verdict.WON, D("400.00"))
    assert s.verdicts["d2"] == (Verdict.WON, D("187.50"))
    assert s.verdicts["m"] == (Verdict.REFUNDED, D(100)) and s.verdicts["w"] == (Verdict.REFUNDED, D(200))
    assert s.commission == 0


@pytest.mark.parametrize("outcome", ["cancelled", "no_contest"])
def test_cancel_and_no_contest_refund_every_stake(outcome):
    bets = [BetLine("m", Side.MERON, D(100)), BetLine("w", Side.WALA, D(200)), BetLine("d", Side.DRAW, D(50), D(8))]
    s = settle(bets, outcome, RATE)
    assert {k: v for k, v in s.verdicts.items()} == {
        "m": (Verdict.REFUNDED, D(100)),
        "w": (Verdict.REFUNDED, D(200)),
        "d": (Verdict.REFUNDED, D(50)),
    }
    assert s.commission == 0


def test_settlement_is_deterministic():
    bets = [BetLine(i, Side.MERON if i % 3 else Side.WALA, D(i) + D("0.07")) for i in range(1, 40)]
    assert settle(bets, "meron", RATE) == settle(bets, "meron", RATE)


def test_obligations_per_result_match_settlement():
    bets = [
        BetLine("m", Side.MERON, D(200)),
        BetLine("w", Side.WALA, D(166)),
        BetLine("d", Side.DRAW, D(50), D(8)),
    ]
    owed = obligations(D(200), D(166), D(50), D(400), RATE)
    for outcome in ("meron", "wala", "draw", "cancelled"):
        paid = sum(p for _, p in settle(bets, outcome, RATE).verdicts.values())
        assert owed[outcome] == paid, outcome
    assert owed == {"meron": D("329.40"), "wala": D("329.40"), "draw": D("766.00"), "cancelled": D("416.00")}
    # One side empty: a MERON or WALA result refunds instead of paying the distributable pool.
    assert obligations(D(500), D(0), D(0), D(0), RATE)["meron"] == D(500)
