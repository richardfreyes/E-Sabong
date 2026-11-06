"""Odds arithmetic for simulated betting. Pure ``Decimal`` functions: nothing here touches the database.

MERON and WALA pay a fixed multiplier, ``SIDE_MULTIPLIER`` (1.83), whatever the pool sizes::

    meron_odds = wala_odds = 1.83
    payout     = stake x 1.83   (stake included)

DRAW is priced separately at a fixed multiplier locked on each bet. Pools are still kept and shown
to players, but they no longer move the MERON/WALA odds. Only ``display_odds`` rounds (half up, 2 dp).

Settlement policy (``settle``):

* MERON or WALA wins: every stake on the winning side pays ``stake x 1.83``; the losing side and
  every DRAW bet get nothing. There is no separate commission: the house margin is in the price.
* DRAW: DRAW bets pay ``stake x locked multiplier``; every MERON and WALA stake is refunded in
  full.
* Cancelled fight or no contest: every stake is refunded.

Each payout rounds half up to the cent.
"""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TypeVar

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Stored precision of final settlement multipliers.
ODDS_PLACES = Decimal("0.0000000001")
# Fixed MERON/WALA payout multiplier (stake included): 183 back for every 100 staked.
SIDE_MULTIPLIER = Decimal("1.83")

K = TypeVar("K", bound=Hashable)


class Side(StrEnum):
    MERON = "meron"
    WALA = "wala"
    DRAW = "draw"


class Verdict(StrEnum):
    WON = "won"
    LOST = "lost"
    REFUNDED = "refunded"


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def display_odds(odds: Decimal | None) -> Decimal | None:
    return None if odds is None else odds.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PoolOdds:
    """Odds for MERON and WALA, or ``None`` for both while either side has no stake."""

    meron: Decimal | None
    wala: Decimal | None

    def for_side(self, side: Side) -> Decimal | None:
        return self.meron if side == Side.MERON else self.wala if side == Side.WALA else None


def pool_odds(meron_pool: Decimal, wala_pool: Decimal, commission_rate: Decimal) -> PoolOdds:
    """MERON/WALA odds: fixed at ``SIDE_MULTIPLIER``. Pools and commission no longer change them."""
    return PoolOdds(SIDE_MULTIPLIER, SIDE_MULTIPLIER)


def estimated_pays(stake: Decimal, odds: Decimal | None) -> Decimal | None:
    """Projected gross return of a MERON or WALA stake at the current, provisional odds."""
    if stake <= 0:
        return ZERO
    return None if odds is None else to_cents(stake * odds)


def fixed_pays(stake: Decimal, multiplier: Decimal) -> Decimal:
    return to_cents(stake * multiplier)


def allocate(pool: Decimal, stakes: Sequence[tuple[K, Decimal]]) -> dict[K, Decimal]:
    """Split ``pool`` (whole cents) pro rata to ``stakes``; the shares sum exactly to ``pool``."""
    total = sum((s for _, s in stakes), ZERO)
    if not stakes or total <= 0:
        return {}
    exact = [pool * s / total for _, s in stakes]
    shares = [e.quantize(CENT, rounding=ROUND_DOWN) for e in exact]
    leftover = int((pool - sum(shares, ZERO)) / CENT)
    by_remainder = sorted(range(len(stakes)), key=lambda i: (-(exact[i] - shares[i]), i))
    for i in by_remainder[:leftover]:
        shares[i] += CENT
    return {key: share for (key, _), share in zip(stakes, shares, strict=True)}


@dataclass(frozen=True)
class BetLine:
    id: Hashable
    side: Side
    amount: Decimal
    multiplier: Decimal | None = None  # DRAW only


@dataclass(frozen=True)
class Settlement:
    verdicts: dict[Hashable, tuple[Verdict, Decimal]]  # bet id -> (verdict, gross payout)
    meron_pool: Decimal
    wala_pool: Decimal
    draw_pool: Decimal
    commission: Decimal  # retained from the MERON/WALA pool; zero when it is refunded
    distributable: Decimal
    meron_odds: Decimal | None  # final multipliers, as locked at close
    wala_odds: Decimal | None


def final_odds(meron_pool: Decimal, wala_pool: Decimal, commission_rate: Decimal) -> PoolOdds:
    odds = pool_odds(meron_pool, wala_pool, commission_rate)
    q = lambda v: None if v is None else v.quantize(ODDS_PLACES, rounding=ROUND_DOWN)  # noqa: E731
    return PoolOdds(q(odds.meron), q(odds.wala))


def settle(bets: Sequence[BetLine], outcome: str, commission_rate: Decimal) -> Settlement:
    """Decide every bet for ``outcome`` (meron, wala, draw, no_contest or cancelled).

    ``bets`` should be in a stable order (placement time, then id)."""
    pools = {side: sum((b.amount for b in bets if b.side == side), ZERO) for side in Side}
    meron, wala = pools[Side.MERON], pools[Side.WALA]
    odds = final_odds(meron, wala, commission_rate)
    verdicts: dict[Hashable, tuple[Verdict, Decimal]] = {}
    refund = lambda b: (Verdict.REFUNDED, b.amount)  # noqa: E731
    lose = (Verdict.LOST, ZERO)
    commission = distributable = ZERO

    if outcome in (Side.MERON, Side.WALA):
        for b in bets:
            verdicts[b.id] = (Verdict.WON, fixed_pays(b.amount, SIDE_MULTIPLIER)) if b.side == outcome else lose
        distributable = sum((pays for verdict, pays in verdicts.values() if verdict == Verdict.WON), ZERO)
    elif outcome == Side.DRAW:
        for b in bets:
            if b.side == Side.DRAW:
                assert b.multiplier is not None, "DRAW bets carry a locked multiplier"
                verdicts[b.id] = (Verdict.WON, fixed_pays(b.amount, b.multiplier))
            else:
                verdicts[b.id] = refund(b)
    else:  # cancelled or no contest
        for b in bets:
            verdicts[b.id] = refund(b)

    return Settlement(
        verdicts=verdicts,
        meron_pool=meron,
        wala_pool=wala,
        draw_pool=pools[Side.DRAW],
        commission=commission,
        distributable=distributable,
        meron_odds=odds.meron,
        wala_odds=odds.wala,
    )


def obligations(
    meron_pool: Decimal, wala_pool: Decimal, draw_pool: Decimal, draw_pays: Decimal, commission_rate: Decimal
) -> dict[str, Decimal]:
    """Total points each possible result would pay back to players (stakes included), per ``settle``.

    MERON or WALA pays ``stake x SIDE_MULTIPLIER`` on the winning side. ``draw_pays`` is the sum of
    ``fixed_pays`` over DRAW bets."""
    two_sided = meron_pool + wala_pool
    meron_owes, wala_owes = fixed_pays(meron_pool, SIDE_MULTIPLIER), fixed_pays(wala_pool, SIDE_MULTIPLIER)
    return {
        Side.MERON.value: meron_owes,
        Side.WALA.value: wala_owes,
        Side.DRAW.value: two_sided + draw_pays,
        "cancelled": two_sided + draw_pool,
    }


def desk_obligations(
    player_meron: Decimal,
    player_wala: Decimal,
    dummy_meron: Decimal,
    dummy_wala: Decimal,
    draw_pool: Decimal,
    draw_pays: Decimal,
    commission_rate: Decimal,
) -> dict[str, Decimal]:
    """What a result pays back to players. Dummy stakes are shown in the totals but never paid,
    so only player stakes count: a win pays ``stake x SIDE_MULTIPLIER``. A draw or cancellation
    refunds player stakes only."""
    player_sides = player_meron + player_wala
    return {
        Side.MERON.value: fixed_pays(player_meron, SIDE_MULTIPLIER),
        Side.WALA.value: fixed_pays(player_wala, SIDE_MULTIPLIER),
        Side.DRAW.value: player_sides + draw_pays,
        "cancelled": player_sides + draw_pool,
    }
