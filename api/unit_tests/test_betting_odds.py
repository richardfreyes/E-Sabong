"""Pool odds and settlement arithmetic (pure functions, no database)."""

from decimal import Decimal as D

import pytest

from app.modules.betting.odds import (
    DEFAULT_ODDS,
    ODDS_SUM,
    BetLine,
    PoolOdds,
    Side,
    Verdict,
    allocate,
    cap_share,
    desk_obligations,
    display_odds,
    estimated_pays,
    fixed_pays,
    manual_odds,
    normalized_probabilities,
    obligations,
    pool_odds,
    settle,
)

RATE = D("0.10")


def test_even_pools_pay_183_and_sum_to_366():
    odds = pool_odds(D(183), D(183), RATE)
    assert odds.meron == odds.wala == DEFAULT_ODDS
    assert odds.meron + odds.wala == ODDS_SUM
    assert normalized_probabilities(odds.meron, odds.wala) == (D("0.5000"), D("0.5000"))


@pytest.mark.parametrize(
    ("meron", "wala", "shown_meron", "shown_wala"),
    [
        ("600", "400", "1.46", "2.20"),  # 3.66 × 400/1000 = 1.464, 3.66 × 600/1000 = 2.196
        ("200", "166", "1.66", "2.00"),  # 200 × 1.83 = 366, the whole pot
        ("780261.90", "906433.56", "1.97", "1.69"),
    ],
)
def test_prices_follow_the_pools_and_sum_to_366(meron, wala, shown_meron, shown_wala):
    odds = pool_odds(D(meron), D(wala), RATE)
    assert odds.meron + odds.wala == ODDS_SUM
    assert (display_odds(odds.meron), display_odds(odds.wala)) == (D(shown_meron), D(shown_wala))
    # Normalized 1/odds is the pool share.
    share = (D(meron) / (D(meron) + D(wala))).quantize(D("0.0001"))
    assert normalized_probabilities(odds.meron, odds.wala)[0] == share


def test_more_money_on_a_side_shortens_it_and_lengthens_the_other():
    even = pool_odds(D(183), D(183), RATE)
    tilted = pool_odds(D(600), D(400), RATE)
    assert tilted.meron < even.meron and tilted.wala > even.wala
    back = pool_odds(D(400), D(600), RATE)
    assert back.meron > even.meron and back.wala < even.wala


@pytest.mark.parametrize(("meron", "wala"), [(0, 0), (0, 500), (500, 0)])
def test_a_missing_side_keeps_the_opening_price(meron, wala):
    odds = pool_odds(D(meron), D(wala), RATE)
    assert odds.meron == DEFAULT_ODDS and odds.wala == DEFAULT_ODDS


def test_a_price_never_falls_below_one_and_the_pair_still_sums_to_366():
    # 95% of the pot would quote under 1. The favorite is floored at 1 and the other side takes 2.66.
    odds = pool_odds(D(95), D(5), RATE)
    assert odds.meron == D(1) and odds.wala == D("2.66")
    assert odds.meron + odds.wala == ODDS_SUM


def test_manual_odds_reject_a_pair_over_366():
    assert manual_odds(D("1.50"), D("2.16")) == manual_odds(D("1.50"), D("2.16"))
    with pytest.raises(ValueError):
        manual_odds(D("2.00"), D("2.00"))
    with pytest.raises(ValueError):
        manual_odds(D("0.90"), D("1.83"))


def test_draw_never_enters_the_two_sided_odds():
    lines = [
        BetLine(1, Side.MERON, D(183), D("1.83")),
        BetLine(2, Side.WALA, D(183), D("1.83")),
        BetLine(3, Side.DRAW, D(972), D(8)),
    ]
    s = settle(lines, "meron", RATE)
    # The pot is 183 + 183 = 366. The winner locked 1.83, so 183 × 1.83 = 334.89, which is under that max.
    assert s.distributable == D("334.89") and s.commission == D("31.11")
    assert s.verdicts[3] == (Verdict.LOST, D("0.00"))


def test_estimated_pays_uses_the_unrounded_price():
    odds = pool_odds(D(600), D(400), RATE)
    assert estimated_pays(D(100), odds.meron) == D("146.40")  # 100 × 1.464
    assert estimated_pays(D(0), odds.meron) == D("0.00")
    assert estimated_pays(D(100), None) is None
    assert fixed_pays(D(50), D(8)) == D("400.00")


def test_allocation_sums_exactly_and_breaks_ties_by_order():
    shares = allocate(D("100.00"), [("a", D(1)), ("b", D(1)), ("c", D(1))])
    assert shares == {"a": D("33.34"), "b": D("33.33"), "c": D("33.33")}
    shares = allocate(D("10.00"), [("x", D("3.33")), ("y", D("6.67")), ("z", D("0.01"))])
    assert sum(shares.values()) == D("10.00")
    assert allocate(D("5.00"), []) == {}


def test_winners_are_paid_the_closing_board_price_not_the_price_they_bet_at():
    bets = [
        BetLine("m1", Side.MERON, D(100), D("1.83")),
        BetLine("m2", Side.MERON, D(100), D("1.83")),
        BetLine("w1", Side.WALA, D(166), D("2.00")),
        BetLine("d1", Side.DRAW, D(50), D(8)),
    ]
    s = settle(bets, "meron", RATE)
    assert (s.meron_pool, s.wala_pool, s.draw_pool) == (D(200), D(166), D(50))
    # Both bet at 1.83; MERON closed at 1.66, so each is paid 100 × 1.66 = 166.
    assert s.verdicts["m1"] == (Verdict.WON, D("166.00")) and s.verdicts["m2"] == (Verdict.WON, D("166.00"))
    assert s.distributable == D(332) and s.commission == D(34)
    assert s.verdicts["w1"] == (Verdict.LOST, D("0.00")) and s.verdicts["d1"][0] == Verdict.LOST
    assert (s.meron_odds, s.wala_odds) == (D("1.66"), D("2.00"))
    # The same bets, if Wala wins: 166 × 2.00 = 332, under the 366 pot.
    wala = settle(bets, "wala", RATE)
    assert wala.verdicts["w1"] == (Verdict.WON, D("332.00"))
    assert wala.commission == D("34.00")


def test_one_sided_win_cannot_pay_more_than_the_pot():
    bets = [BetLine("m", Side.MERON, D(500), D("1.83")), BetLine("d", Side.DRAW, D(20), D(8))]
    won = settle(bets, "meron", RATE)
    assert won.verdicts["m"] == (Verdict.WON, D(500))  # 500 × 1.83 = 915, and the pot is 500
    assert won.verdicts["d"] == (Verdict.LOST, D("0.00"))
    assert won.commission == 0 and won.meron_odds == DEFAULT_ODDS
    lost = settle(bets, "wala", RATE)
    assert lost.verdicts["m"] == (Verdict.LOST, D("0.00"))
    assert lost.commission == D(500)


def test_payout_is_stake_times_the_2_decimal_price_shown():
    # 4,413 vs 3,532 shows 1.63 / 2.03. 100 × 1.63 = 163.00 and 100 × 2.03 = 203.00, not the unrounded price.
    bets = [BetLine("m", Side.MERON, D(4413), D("1.6012")), BetLine("w", Side.WALA, D(3532), D("2.0329"))]
    meron, wala = settle(bets, "meron", RATE), settle(bets, "wala", RATE)
    assert (meron.meron_odds, meron.wala_odds) == (D("1.63"), D("2.03"))
    assert meron.verdicts["m"] == (Verdict.WON, D("7193.19"))  # 4,413 × 1.63
    assert wala.verdicts["w"] == (Verdict.WON, D("7169.96"))  # 3,532 × 2.03
    assert cap_share(D("163.00"), D("183.00"), D(500)) == D("163.00")


def test_a_declarator_price_is_paid_when_given():
    bets = [BetLine("m", Side.MERON, D(100), D("1.83")), BetLine("w", Side.WALA, D(100), D("2.16"))]
    s = settle(bets, "meron", RATE, PoolOdds(D("1.50"), D("2.16")))
    assert s.verdicts["m"] == (Verdict.WON, D("150.00")) and s.meron_odds == D("1.50")


def test_draw_pays_fixed_and_refunds_both_sides():
    bets = [
        BetLine("m", Side.MERON, D(100), D("1.83")),
        BetLine("w", Side.WALA, D(200), D("1.83")),
        BetLine("d1", Side.DRAW, D(50), D(8)),
        BetLine("d2", Side.DRAW, D(25), D("7.50")),
    ]
    s = settle(bets, "draw", RATE)
    assert s.verdicts["d1"] == (Verdict.WON, D("400.00"))
    assert s.verdicts["d2"] == (Verdict.WON, D("187.50"))
    assert s.verdicts["m"] == (Verdict.REFUNDED, D(100)) and s.verdicts["w"] == (Verdict.REFUNDED, D(200))
    assert s.commission == 0


@pytest.mark.parametrize("outcome", ["cancelled", "no_contest"])
def test_cancel_and_no_contest_refund_every_stake(outcome):
    bets = [
        BetLine("m", Side.MERON, D(100), D("1.83")),
        BetLine("w", Side.WALA, D(200), D("1.83")),
        BetLine("d", Side.DRAW, D(50), D(8)),
    ]
    s = settle(bets, outcome, RATE)
    assert {k: v for k, v in s.verdicts.items()} == {
        "m": (Verdict.REFUNDED, D(100)),
        "w": (Verdict.REFUNDED, D(200)),
        "d": (Verdict.REFUNDED, D(50)),
    }
    assert s.commission == 0


def test_settlement_is_deterministic():
    bets = [BetLine(i, Side.MERON if i % 3 else Side.WALA, D(i) + D("0.07"), D("1.83")) for i in range(1, 40)]
    assert settle(bets, "meron", RATE) == settle(bets, "meron", RATE)


def test_obligations_match_the_current_price_and_the_pot_cap():
    owed = obligations(D(200), D(166), D(50), D(400), RATE)
    assert owed["meron"] == D("332.00") and owed["wala"] == D("332.00")
    assert owed["draw"] == D("766.00") and owed["cancelled"] == D("416.00")
    # 500 × 1.83 is 915, and there is no opposing stake, so the pot of 500 is the max.
    solo = obligations(D(500), D(0), D(0), D(0), RATE)
    assert solo["meron"] == D(500) and solo["wala"] == D(0)


def test_dummy_stake_moves_the_price_and_is_not_paid():
    assert display_odds(pool_odds(D(300), D(300), RATE).meron) == D("1.83")
    owed = desk_obligations(D(300), D(0), D(0), D(300), D(0), D(0), RATE, D("549.00"), D(0))
    assert owed["meron"] == D("549.00") and owed["wala"] == D("0.00")
    # Locked returns already above the pot are cut back to it.
    capped = desk_obligations(D(600), D(100), D(0), D(0), D(0), D(0), RATE, D("1200.00"), D("183.00"))
    assert capped["meron"] == D("700.00") and capped["wala"] == D("183.00")


def test_reference_screenshot_probability_is_the_pool_share():
    meron, wala = D("780261.90"), D("906433.56")
    odds = pool_odds(meron, wala, RATE)
    share = (meron / (meron + wala)).quantize(D("0.0001"))
    assert normalized_probabilities(odds.meron, odds.wala)[0] == share
