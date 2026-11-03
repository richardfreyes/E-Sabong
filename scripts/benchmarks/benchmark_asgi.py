"""Benchmark-only timing observers; no product endpoint or wire field changes.

Two trace stages per event, appended to a private per-worker JSONL file:

* ``committed``: the after-commit hand-off to the room broadcaster, before any ODDS_UPDATED
  coalescing delay. The runner measures latency from here.
* ``published``: immediately before the Redis publish of a packet that actually went out.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
from app.core.redis import get_redis
from app.main import app
from app.modules.simulations import broadcast

__all__ = ["app"]

TYPES = ("MATCH_STATE_CHANGED", "ODDS_UPDATED")
redis = get_redis()
original = redis.publish
trace = Path(os.environ["BENCHMARK_TRACE_DIR"]) / f"publisher-{os.getpid()}.jsonl"
fd = os.open(trace, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)


def record(stage: str, channel: str, payload: dict) -> None:
    data = payload["data"]
    row = {
        "stage": stage,
        "epoch_ms": time.time_ns() / 1e6,
        "room": channel,
        "type": payload["type"],
        "match_id": data["matchId"],
        "version": data["version"],
        "pid": os.getpid(),
    }
    os.write(fd, (json.dumps(row) + "\n").encode())


def committed(channel: str, payload: dict) -> None:
    if channel.startswith("tenant:") and payload.get("type") in TYPES:
        record("committed", channel, payload)


async def observed(channel, message, *args, **kwargs):
    if isinstance(channel, str) and channel.startswith("tenant:"):
        payload = json.loads(message)
        if payload.get("type") in TYPES:
            record("published", channel, payload)
    return await original(channel, message, *args, **kwargs)


redis.publish = observed
broadcast.committed_observer = committed
