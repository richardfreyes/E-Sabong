"""Odds arithmetic for simulated betting. Pure ``Decimal`` functions: nothing here touches the database.

MERON and WALA open at 1.83. Once both sides have points, the two prices move with the pools
and always sum to 3.66 — 183 + 183 = 366, and 366 is the maximum the two prices may add to::

    meron_odds = 3.66 × wala_pool / (meron_pool + wala_pool)
    wala_odds  = 3.66 × meron_pool / (meron_pool + wala_pool)

A heavier pool gets the shorter price. A price is never quoted below 1; the other side then
keeps the rest of the 3.66. The pool deduction does not move these prices: the margin is the
gap between 3.66 and a fair 4.00.

Implied probability is ``1 / odds``. The two implied probabilities are scaled so they sum to 1
(1.83 and 1.83 is 50% / 50%).

MERON and WALA pay ``stake × the odds shown on the board`` — the price rounded to 2 decimal
places, as fixed when betting closes. 100 at 1.63 pays 163.00. The price a bet was placed at is
recorded on the bet but is not what it is paid. The amount paid to the winning side never exceeds
the pot (MERON pool + WALA pool). DRAW stays a fixed multiplier locked on the bet, and its pool
never enters the MERON/WALA price.

Settlement policy (``settle``):

* MERON or WALA wins: every stake on the winning side pays the closing odds, scaled down so the
  winning payouts sum to the pot when they would otherwise exceed it. The losing side and every
  DRAW bet get nothing. What the pot does not pay is the house margin.
* DRAW: DRAW bets pay ``stake × locked multiplier``; every MERON and WALA stake is refunded.
* Cancelled fight or no contest: every stake is refunded.

Each payout rounds half up to the cent. When a side is scaled to the pot, the shares sum to the
pot exactly.
"""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TypeVar

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Locked onto a bet: 4 decimal places, half up.
LOCKED_PLACES = Decimal("0.0001")
# Opening price, and the even-pool price. 1.83 + 1.83 = 3.66.
DEFAULT_ODDS = Decimal("1.83")
ODDS_SUM = Decimal("3.66")
MIN_ODDS = Decimal("1")

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


def lock_odds(odds: Decimal) -> Decimal:
    """The price stored on a bet. Never below 1, and never long enough that the pair could pass 3.66."""
    locked = odds.quantize(LOCKED_PLACES, rounding=ROUND_HALF_UP)
    return MIN_ODDS if locked < MIN_ODDS else locked


@dataclass(frozen=True)
class PoolOdds:
    """Odds for MERON and WALA. Both are 1.83 until each side has a stake."""

    meron: Decimal | None
    wala: Decimal | None

    def for_side(self, side: Side) -> Decimal | None:
        return self.meron if side == Side.MERON else self.wala if side == Side.WALA else None


def pool_odds(meron_pool: Decimal, wala_pool: Decimal, commission_rate: Decimal) -> PoolOdds:
    """MERON/WALA prices. ``commission_rate`` is accepted so callers stay stable; it does not move the price.

    With one side empty both prices stay at 1.83. Otherwise they sum to 3.66 and follow the pools.
    """
    del commission_rate
    if meron_pool <= 0 or wala_pool <= 0:
        return PoolOdds(DEFAULT_ODDS, DEFAULT_ODDS)
    total = meron_pool + wala_pool
    meron = ODDS_SUM * wala_pool / total
    wala = ODDS_SUM * meron_pool / total
    if meron < MIN_ODDS:
        return PoolOdds(MIN_ODDS, ODDS_SUM - MIN_ODDS)
    if wala < MIN_ODDS:
        return PoolOdds(ODDS_SUM - MIN_ODDS, MIN_ODDS)
    return PoolOdds(meron, wala)


def normalized_probabilities(meron: Decimal | None, wala: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    """Scale ``1 / odds`` so the two sides sum to 1. ``None`` until both prices exist."""
    if meron is None or wala is None or meron <= 0 or wala <= 0:
        return None, None
    implied_meron, implied_wala = Decimal(1) / meron, Decimal(1) / wala
    total = implied_meron + implied_wala
    places = Decimal("0.0001")
    return (
        (implied_meron / total).quantize(places, rounding=ROUND_HALF_UP),
        (implied_wala / total).quantize(places, rounding=ROUND_HALF_UP),
    )


def manual_odds(meron: Decimal, wala: Decimal) -> PoolOdds:
    """A Declarator's price. Each side is at least 1 and the pair sums to at most 3.66."""
    if meron < MIN_ODDS or wala < MIN_ODDS:
        raise ValueError("Each price has to be at least 1.")
    if meron + wala > ODDS_SUM:
        raise ValueError("Meron and Wala odds sum to more than 3.66.")
    return PoolOdds(meron, wala)


def estimated_pays(stake: Decimal, odds: Decimal | None) -> Decimal | None:
    """Projected gross return of a MERON or WALA stake at the current, provisional odds."""
    if stake <= 0:
        return ZERO
    return None if odds is None else to_cents(stake * odds)


def fixed_pays(stake: Decimal, multiplier: Decimal) -> Decimal:
    return to_cents(stake * multiplier)


def cap_share(raw: Decimal, side_claim: Decimal, pot: Decimal) -> Decimal:
    """This stake's return once the winning side is limited to ``pot``.

    ``raw`` is this stake at its locked odds. ``side_claim`` is every locked return on that side.
    When the claims fit in the pot, ``raw`` is paid in full.
    """
    if raw <= 0:
        return ZERO
    if side_claim <= 0 or side_claim <= pot:
        return raw
    return to_cents(raw * pot / side_claim)


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
    multiplier: Decimal | None = None  # locked odds; DRAW uses its own multiplier


@dataclass(frozen=True)
class Settlement:
    verdicts: dict[Hashable, tuple[Verdict, Decimal]]  # bet id -> (verdict, gross payout)
    meron_pool: Decimal
    wala_pool: Decimal
    draw_pool: Decimal
    commission: Decimal  # what the pot does not pay back; zero when the pot is refunded
    distributable: Decimal
    meron_odds: Decimal | None  # prices in force at settlement
    wala_odds: Decimal | None


def _dummy(bet: BetLine) -> bool:
    return isinstance(bet.id, str) and bet.id.startswith("dummy:")


def shown(odds: PoolOdds) -> PoolOdds:
    """The price as players see it, and as MERON/WALA are paid: 2 decimal places, half up."""
    return PoolOdds(display_odds(odds.meron), display_odds(odds.wala))


def final_odds(meron_pool: Decimal, wala_pool: Decimal, commission_rate: Decimal) -> PoolOdds:
    return shown(pool_odds(meron_pool, wala_pool, commission_rate))


def settle(
    bets: Sequence[BetLine], outcome: str, commission_rate: Decimal, prices: PoolOdds | None = None
) -> Settlement:
    """Decide every bet for ``outcome`` (meron, wala, draw, no_contest or cancelled).

    ``bets`` should be in a stable order (placement time, then id). ``prices`` is the board price
    fixed at close (a Declarator's pair included); without it the pools are priced. Every MERON or
    WALA line is paid ``stake × that price`` at 2 decimal places. Dummy lines sit in the pot and are
    never paid.
    """
    pools = {side: sum((b.amount for b in bets if b.side == side), ZERO) for side in Side}
    meron, wala = pools[Side.MERON], pools[Side.WALA]
    odds = shown(prices) if prices is not None and prices.meron is not None and prices.wala is not None else final_odds(
        meron, wala, commission_rate
    )
    verdicts: dict[Hashable, tuple[Verdict, Decimal]] = {}
    refund = lambda b: (Verdict.REFUNDED, b.amount)  # noqa: E731
    lose = (Verdict.LOST, ZERO)
    commission = distributable = ZERO

    if outcome in (Side.MERON, Side.WALA):
        closing = odds.for_side(Side(outcome)) or DEFAULT_ODDS
        claims: list[tuple[Hashable, Decimal]] = []
        for b in bets:
            if b.side != outcome or _dummy(b):
                continue
            claims.append((b.id, fixed_pays(b.amount, closing)))
        pot = meron + wala
        claimed = sum((pays for _, pays in claims), ZERO)
        if claimed > pot:
            shares = allocate(pot, claims)
            distributable = pot
        else:
            shares = dict(claims)
            distributable = claimed
        commission = pot - distributable
        for b in bets:
            verdicts[b.id] = (Verdict.WON, shares[b.id]) if b.id in shares else lose
    elif outcome == Side.DRAW:
        for b in bets:
            if b.side == Side.DRAW and not _dummy(b):
                assert b.multiplier is not None, "DRAW bets carry a locked multiplier"
                verdicts[b.id] = (Verdict.WON, fixed_pays(b.amount, b.multiplier))
            elif b.side == Side.DRAW:
                verdicts[b.id] = lose
            else:
                verdicts[b.id] = refund(b)
    else:  # cancelled or no contest
        for b in bets:
            verdicts[b.id] = refund(b) if not _dummy(b) else lose

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
    """What each result pays back if every stake on a side were at the current price, capped at the pot.

    ``draw_pays`` is the sum of ``fixed_pays`` over DRAW bets. A draw or cancellation refunds both
    side pools and does not take the margin."""
    price = pool_odds(meron_pool, wala_pool, commission_rate)
    pot = meron_pool + wala_pool
    meron_owes = fixed_pays(meron_pool, price.meron) if price.meron is not None and meron_pool > 0 else ZERO
    wala_owes = fixed_pays(wala_pool, price.wala) if price.wala is not None and wala_pool > 0 else ZERO
    return {
        Side.MERON.value: meron_owes if meron_owes <= pot else pot,
        Side.WALA.value: wala_owes if wala_owes <= pot else pot,
        Side.DRAW.value: pot + draw_pays,
        "cancelled": pot + draw_pool,
    }


def desk_obligations(
    player_meron: Decimal,
    player_wala: Decimal,
    dummy_meron: Decimal,
    dummy_wala: Decimal,
    draw_pool: Decimal,
    draw_pays: Decimal,
    commission_rate: Decimal,
    meron_pays: Decimal | None = None,
    wala_pays: Decimal | None = None,
) -> dict[str, Decimal]:
    """What a result pays back to players.

    ``meron_pays`` and ``wala_pays`` are the locked returns already accepted. When they are omitted,
    the current price is used. Either way the winning side cannot be paid more than the pot.
    Dummy stakes are in the pot and are never paid. A draw or cancellation refunds player stakes only.
    """
    pot = player_meron + dummy_meron + player_wala + dummy_wala
    if meron_pays is None or wala_pays is None:
        estimated = obligations(player_meron + dummy_meron, player_wala + dummy_wala, draw_pool, draw_pays, commission_rate)
        # The estimate prices the whole pot. Scale it back to the player stake on that side.
        def player_share(player: Decimal, total: Decimal, owed: Decimal) -> Decimal:
            if player <= 0 or total <= 0:
                return ZERO
            return owed if player == total else to_cents(owed * player / total)

        meron_pays = player_share(player_meron, player_meron + dummy_meron, estimated[Side.MERON.value])
        wala_pays = player_share(player_wala, player_wala + dummy_wala, estimated[Side.WALA.value])
    player_sides = player_meron + player_wala
    return {
        Side.MERON.value: meron_pays if meron_pays <= pot else pot,
        Side.WALA.value: wala_pays if wala_pays <= pot else pot,
        Side.DRAW.value: player_sides + draw_pays,
        "cancelled": player_sides + draw_pool,
    }
