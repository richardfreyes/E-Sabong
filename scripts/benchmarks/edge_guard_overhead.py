#!/usr/bin/env python3
"""Latency the edge guard (api/app/security) adds to one API request, by mode.

    cd api && .venv/bin/python ../scripts/benchmarks/edge_guard_overhead.py [--requests 4000] [--concurrency 50]

The guarded app is a trivial ASGI endpoint, so the numbers are the guard's own cost: one Redis
Lua round trip (block check + two counters), JWT verification for signed-in requests, and the
classification. Uses Redis database 13 of REDIS_URL (default: the local dev Redis) and deletes
only its own sec:* keys afterwards. Not for production hosts.
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REDIS_URL", "redis://:dev-redis@localhost:6379/13")
os.environ.setdefault("JWT_SECRET", "bench-secret-0123456789abcdef0123456789abcdef")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

import httpx  # noqa: E402
from app.core.redis import close_redis, get_redis  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.security.config import SecuritySettings  # noqa: E402
from app.security.middleware import EdgeGuardMiddleware  # noqa: E402


async def endpoint(scope, receive, send):  # type: ignore[no-untyped-def]
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


def from_ip(app, ip: str):  # type: ignore[no-untyped-def]
    async def shim(scope, receive, send):  # type: ignore[no-untyped-def]
        await app({**scope, "client": (ip, 40000)}, receive, send)

    return shim


async def measure(app, n: int, concurrency: int, headers: dict[str, str]) -> tuple[list[float], float]:  # type: ignore[no-untyped-def]
    latencies: list[float] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench.local") as client:
        for _ in range(200):  # warm-up (script registration, pools)
            await client.get("/api/v1/auth/me", headers=headers)
        queue = iter(range(n))

        async def worker() -> None:
            for _ in queue:
                t = time.perf_counter()
                r = await client.get("/api/v1/auth/me", headers=headers)
                latencies.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, r.status_code

        started = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed = time.perf_counter() - started
    return latencies, n / elapsed


def q(values: list[float], p: float) -> float:
    return sorted(values)[min(len(values) - 1, int(p * len(values)))]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=4000)
    ap.add_argument("--concurrency", type=int, default=50)
    args = ap.parse_args()
    token, _ = create_access_token(subject_id=uuid.uuid4(), audience="tenant", tenant_id=uuid.uuid4(), family=uuid.uuid4())
    limits = {"ip_per_minute": 10**9, "user_per_minute": 10**9, "anon_per_minute": 10**9, "env_name": "bench"}
    rows = []
    try:
        for concurrency in (1, args.concurrency):
            base = None
            for mode in ("off", "monitor", "enforce"):
                for who, headers in (("anonymous", {}), ("signed-in", {"Authorization": f"Bearer {token}"})):
                    app = EdgeGuardMiddleware(endpoint, SecuritySettings(guard_mode=mode, **limits))
                    client_ip = f"198.18.{concurrency % 250}.{len(rows) + 1}"
                    lat, rps = await measure(from_ip(app, client_ip), args.requests, concurrency, headers)
                    p50, p95, p99 = q(lat, 0.5), q(lat, 0.95), q(lat, 0.99)
                    if mode == "off" and who == "anonymous":
                        base = p50
                    rows.append((concurrency, mode, who, p50, p95, p99, rps, p50 - (base or p50)))
    finally:
        redis = get_redis()
        keys = [k async for k in redis.scan_iter(match="sec:*", count=1000)]
        if keys:
            await redis.delete(*keys)
        await close_redis()
    print(f"{args.requests} requests per row; trivial endpoint; local Redis\n")
    print(f"{'conc':>4} {'mode':<8} {'caller':<10} {'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'req/s':>8} {'p50 vs off':>11}")
    for c, mode, who, p50, p95, p99, rps, delta in rows:
        print(f"{c:>4} {mode:<8} {who:<10} {p50:7.3f} {p95:7.3f} {p99:7.3f} {rps:8.0f} {delta:+10.3f}")


if __name__ == "__main__":
    asyncio.run(main())
