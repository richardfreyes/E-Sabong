"""Post-commit fan-out of simulation room events through Redis, with ODDS_UPDATED coalescing.

Every committed wager produces one ODDS_UPDATED offer. Per match, this worker publishes the
first offer immediately (leading edge) and merges any further offers that arrive within
``ODDS_WINDOW_SECONDS`` into one trailing packet carrying the newest totals/version and the
summed stake deltas. An isolated wager is therefore never delayed; a burst costs at most one
publish per match per window per worker instead of one per wager.

Totals and ``version`` are authoritative; ``deltaSideA``/``deltaSideB`` only describe the
stakes merged into this packet. Several API workers coalesce independently, so clients must
apply a match update only when its version is newer than the last one they applied.
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.core.redis import get_redis

ODDS_WINDOW_SECONDS = 0.05
ODDS = "ODDS_UPDATED"
_PRUNE_ABOVE = 1024
_CENT = Decimal("0.01")

# Benchmark/diagnostic hook, called synchronously with (channel, payload) when a committed event
# is handed to this module, before any coalescing delay. None in normal operation.
committed_observer: Callable[[str, dict[str, Any]], None] | None = None


@dataclass
class _Pending:
    channel: str
    payload: dict[str, Any]
    delta_a: Decimal = Decimal(0)
    delta_b: Decimal = Decimal(0)


@dataclass
class _MatchState:
    last_flush: float = float("-inf")
    pending: _Pending | None = None
    timer: asyncio.TimerHandle | None = None
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


_matches: dict[str, _MatchState] = {}


async def _publish(channel: str, payload: dict[str, Any]) -> None:
    await get_redis().publish(channel, json.dumps(jsonable_encoder(payload), separators=(",", ":")))


def _packet(pending: _Pending) -> dict[str, Any]:
    data = {
        **pending.payload["data"],
        "deltaSideA": str(pending.delta_a.quantize(_CENT)),
        "deltaSideB": str(pending.delta_b.quantize(_CENT)),
        "timestamp": time.time_ns() // 1_000_000,
    }
    return {**pending.payload, "data": data}


async def _flush(key: str) -> None:
    state = _matches.get(key)
    if state is None or state.pending is None:
        return
    if state.timer is not None:
        state.timer.cancel()
        state.timer = None
    pending, state.pending = state.pending, None
    state.last_flush = time.monotonic()
    await _publish(pending.channel, _packet(pending))


def _prune(now: float) -> None:
    if len(_matches) <= _PRUNE_ABOVE:
        return
    for key in [k for k, s in _matches.items() if s.pending is None and now - s.last_flush > ODDS_WINDOW_SECONDS]:
        del _matches[key]


def _schedule(key: str, state: _MatchState, delay: float) -> None:
    loop = asyncio.get_running_loop()

    def fire() -> None:
        state.timer = None
        task = loop.create_task(_flush(key))
        state.tasks.add(task)
        task.add_done_callback(state.tasks.discard)

    state.timer = loop.call_later(delay, fire)


async def _offer_odds(channel: str, payload: dict[str, Any]) -> None:
    data = payload["data"]
    key = data["matchId"]
    delta_a, delta_b = Decimal(data.pop("deltaSideA", "0")), Decimal(data.pop("deltaSideB", "0"))
    now = time.monotonic()
    _prune(now)
    state = _matches.setdefault(key, _MatchState())
    if state.pending is None:
        state.pending = _Pending(channel, payload, delta_a, delta_b)
    else:
        state.pending.delta_a += delta_a
        state.pending.delta_b += delta_b
        if data["version"] > state.pending.payload["data"]["version"]:
            state.pending.payload = payload
    if now - state.last_flush >= ODDS_WINDOW_SECONDS:
        await _flush(key)  # Leading edge: nothing went out for this match within the window.
    elif state.timer is None:
        _schedule(key, state, state.last_flush + ODDS_WINDOW_SECONDS - now)


async def deliver(channel: str, payload: dict[str, Any]) -> None:
    """Publish a committed room event. Runs from the request's after-commit callbacks."""
    if committed_observer is not None:
        committed_observer(channel, payload)
    if payload["type"] == ODDS:
        await _offer_odds(channel, payload)
        return
    # Keep per-match order within this worker: buffered odds go out before a state change.
    await _flush(payload["data"]["matchId"])
    await _publish(channel, payload)
