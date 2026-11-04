"""Pool arithmetic for simulated betting. Pure ``Decimal`` functions: nothing here touches the database.

MERON and WALA are pari-mutuel against each other::

    total         = meron_pool + wala_pool
    distributable = total x (1 - commission_rate)
    meron_odds    = distributable / meron_pool
    wala_odds     = distributable / wala_pool

DRAW is priced separately at a fixed multiplier locked on each bet, and its pool never enters
the MERON/WALA odds. Odds are kept unrounded; only ``display_odds`` rounds (half up, 2 dp).

Settlement policy (``settle``):

* MERON or WALA wins: the winning side splits the distributable pool pro rata to stake; the
  losing side and every DRAW bet get nothing. The commission is the house's deduction.
* MERON or WALA wins but either side's pool is empty: there is no counterparty, so every MERON
  and WALA stake is refunded in full and no commission is taken. DRAW bets still lose.
* DRAW: DRAW bets pay ``stake x locked multiplier``; every MERON and WALA stake is refunded in
  full and no commission is taken.
* Cancelled fight or no contest: every stake is refunded.

Cent rounding is deterministic: the commission rounds half up to the cent, then each winning
share is floored to the cent and the leftover cents go one at a time to the largest
remainders, ties broken by bet order. Winning payouts therefore sum exactly to the
distributable pool, and ``stakes = payouts + commission`` holds to the cent.
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
    if meron_pool <= 0 or wala_pool <= 0:
        return PoolOdds(None, None)
    distributable = (meron_pool + wala_pool) * (1 - commission_rate)
    return PoolOdds(distributable / meron_pool, distributable / wala_pool)


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

    ``bets`` must be in a stable order (placement time, then id): it breaks rounding ties."""
    pools = {side: sum((b.amount for b in bets if b.side == side), ZERO) for side in Side}
    meron, wala = pools[Side.MERON], pools[Side.WALA]
    odds = final_odds(meron, wala, commission_rate)
    verdicts: dict[Hashable, tuple[Verdict, Decimal]] = {}
    refund = lambda b: (Verdict.REFUNDED, b.amount)  # noqa: E731
    lose = (Verdict.LOST, ZERO)
    commission = distributable = ZERO

    if outcome in (Side.MERON, Side.WALA) and meron > 0 and wala > 0:
        commission = to_cents((meron + wala) * commission_rate)
        distributable = meron + wala - commission
        winners = [(b.id, b.amount) for b in bets if b.side == outcome]
        shares = allocate(distributable, winners)
        for b in bets:
            verdicts[b.id] = (Verdict.WON, shares[b.id]) if b.side == outcome else lose
    elif outcome in (Side.MERON, Side.WALA):
        for b in bets:
            verdicts[b.id] = lose if b.side == Side.DRAW else refund(b)
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

    MERON and WALA cost the same, the distributable pool, because the pool is shared; with a side
    empty they refund both sides instead. ``draw_pays`` is the sum of ``fixed_pays`` over DRAW bets."""
    two_sided = meron_pool + wala_pool
    side_win = two_sided - to_cents(two_sided * commission_rate) if meron_pool > 0 and wala_pool > 0 else two_sided
    return {
        Side.MERON.value: side_win,
        Side.WALA.value: side_win,
        Side.DRAW.value: two_sided + draw_pays,
        "cancelled": two_sided + draw_pool,
    }
