"""Small HTTP load probe for a running stack. Measures; never writes data.

    .venv/bin/python scripts/loadtest.py --base https://demo.localhost --insecure \
        --path /api/health --path /api/v1/public/tenant --requests 2000 --concurrency 50

Reports throughput, latency percentiles and non-2xx counts per path. Run it from a machine
other than the server for meaningful production numbers: on one laptop the load generator
and the stack compete for the same CPU.
"""

import argparse
import asyncio
import os
import socket
import statistics
import time

import httpx


async def probe(client: httpx.AsyncClient, path: str, total: int, concurrency: int) -> dict[str, object]:
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    errors = 0
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)

    async def worker() -> None:
        nonlocal errors
        while not queue.empty():
            queue.get_nowait()
            start = time.perf_counter()
            try:
                r = await client.get(path)
                statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
            except httpx.HTTPError:
                errors += 1
                continue
            latencies.append((time.perf_counter() - start) * 1000)

    began = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - began
    latencies.sort()

    def pct(p: float) -> float:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else float("nan")

    return {
        "path": path,
        "requests": total,
        "concurrency": concurrency,
        "seconds": round(elapsed, 2),
        "rps": round(total / elapsed, 1),
        "p50_ms": round(pct(0.50), 1),
        "p95_ms": round(pct(0.95), 1),
        "p99_ms": round(pct(0.99), 1),
        "max_ms": round(latencies[-1], 1) if latencies else None,
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else None,
        "statuses": statuses,
        "transport_errors": errors,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--insecure", action="store_true", help="accept a local/internal CA certificate")
    parser.add_argument("--resolve", action="append", default=[], help="host=address, for staging without DNS")
    parser.add_argument("--bearer-env", help="name of an env var holding an access token for authenticated paths")
    args = parser.parse_args()
    mapping = dict(item.split("=", 1) for item in args.resolve)
    if mapping:
        original = socket.getaddrinfo

        def getaddrinfo(host, *a, **k):  # type: ignore[no-untyped-def]
            name = host.decode() if isinstance(host, bytes) else host
            return original(mapping.get(name, host), *a, **k)

        socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    headers = {"Authorization": f"Bearer {os.environ[args.bearer_env]}"} if args.bearer_env else {}
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(
        base_url=args.base, verify=not args.insecure, timeout=30, limits=limits, headers=headers
    ) as client:
        for path in args.path:
            await client.get(path)  # warm-up
            print(await probe(client, path, args.requests, args.concurrency))


if __name__ == "__main__":
    asyncio.run(main())
