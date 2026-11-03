"""Benchmark-only pre-publish timing observer; no product endpoint or wire field changes."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
from app.core.redis import get_redis
from app.main import app

__all__ = ["app"]

redis = get_redis()
original = redis.publish
trace = Path(os.environ["BENCHMARK_TRACE_DIR"]) / f"publisher-{os.getpid()}.jsonl"
fd = os.open(trace, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)


async def observed(channel, message, *args, **kwargs):
    if isinstance(channel, str) and channel.startswith("tenant:"):
        payload = json.loads(message)
        if payload.get("type") in ("MATCH_STATE_CHANGED", "ODDS_UPDATED"):
            data = payload["data"]
            record = {
                "epoch_ms": time.time_ns() / 1e6,
                "room": channel,
                "type": payload["type"],
                "match_id": data["matchId"],
                "version": data["version"],
                "pid": os.getpid(),
            }
            os.write(fd, (json.dumps(record) + "\n").encode())
    return await original(channel, message, *args, **kwargs)


redis.publish = observed
