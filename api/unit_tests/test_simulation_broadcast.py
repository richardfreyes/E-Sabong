"""Offline checks of ODDS_UPDATED coalescing; Redis publish is replaced by a recorder."""

import asyncio
import json

import pytest

from app.modules.simulations import broadcast

ROOM = "tenant:t:arena:a"


@pytest.fixture
def published(monkeypatch):  # type: ignore[no-untyped-def]
    sent: list[dict] = []

    class Recorder:
        async def publish(self, channel, message):  # type: ignore[no-untyped-def]
            assert channel == ROOM
            sent.append(json.loads(message))

    monkeypatch.setattr(broadcast, "get_redis", lambda: Recorder())
    monkeypatch.setattr(broadcast, "_matches", {})
    return sent


def odds(version: int, total_a: str, delta_a: str = "0", delta_b: str = "0", match: str = "m1") -> dict:
    return {
        "type": "ODDS_UPDATED",
        "room": ROOM,
        "data": {
            "matchId": match,
            "version": version,
            "totalSideA": total_a,
            "totalSideB": "0.00",
            "deltaSideA": delta_a,
            "deltaSideB": delta_b,
        },
    }


async def test_isolated_update_is_published_immediately(published):  # type: ignore[no-untyped-def]
    await broadcast.deliver(ROOM, odds(2, "5.00", "5.00"))
    assert len(published) == 1
    data = published[0]["data"]
    assert (data["version"], data["totalSideA"], data["deltaSideA"], data["deltaSideB"]) == (2, "5.00", "5.00", "0.00")
    assert isinstance(data["timestamp"], int)


async def test_burst_merges_into_one_trailing_packet_with_summed_deltas(published):  # type: ignore[no-untyped-def]
    await broadcast.deliver(ROOM, odds(2, "1.00", "1.00"))
    # Out-of-order commits inside the window: newest version wins, deltas add up.
    await broadcast.deliver(ROOM, odds(4, "3.50", "2.00", "0.50"))
    await broadcast.deliver(ROOM, odds(3, "3.00", "0.50"))
    assert len(published) == 1
    await asyncio.sleep(broadcast.ODDS_WINDOW_SECONDS * 2)
    assert len(published) == 2
    data = published[1]["data"]
    assert (data["version"], data["totalSideA"], data["deltaSideA"], data["deltaSideB"]) == (4, "3.50", "2.50", "0.50")


async def test_state_change_flushes_buffered_odds_first(published):  # type: ignore[no-untyped-def]
    await broadcast.deliver(ROOM, odds(2, "1.00", "1.00"))
    await broadcast.deliver(ROOM, odds(3, "2.00", "1.00"))
    state = {"type": "MATCH_STATE_CHANGED", "room": ROOM, "data": {"matchId": "m1", "version": 4}}
    await broadcast.deliver(ROOM, state)
    assert [(p["type"], p["data"]["version"]) for p in published] == [
        ("ODDS_UPDATED", 2),
        ("ODDS_UPDATED", 3),
        ("MATCH_STATE_CHANGED", 4),
    ]
    await asyncio.sleep(broadcast.ODDS_WINDOW_SECONDS * 2)
    assert len(published) == 3  # The cancelled timer did not publish a duplicate.


async def test_matches_are_coalesced_independently(published):  # type: ignore[no-untyped-def]
    await broadcast.deliver(ROOM, odds(2, "1.00", "1.00", match="m1"))
    await broadcast.deliver(ROOM, odds(7, "9.00", "9.00", match="m2"))
    assert [p["data"]["matchId"] for p in published] == ["m1", "m2"]


async def test_committed_observer_sees_every_offer_before_coalescing(published, monkeypatch):  # type: ignore[no-untyped-def]
    seen: list[int] = []
    monkeypatch.setattr(broadcast, "committed_observer", lambda channel, payload: seen.append(payload["data"]["version"]))
    for version in (2, 3, 4):
        await broadcast.deliver(ROOM, odds(version, "1.00", "1.00"))
    assert seen == [2, 3, 4] and len(published) == 1
