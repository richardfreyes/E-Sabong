#!/usr/bin/env python3
"""Summarize one observed UAT run: uat_observe.py samples + the API request log + the smoke result.

    uat_summary.py RUN_DIR [RUN_DIR ...]          (each holds observer.jsonl, api.jsonl, result.json)

Prints one JSON document per run (and writes RUN_DIR/summary.json):
- containers: average/peak CPU in cores, share of CFS periods throttled, time stalled on CPU (PSI some),
  peak memory;
- host CPU utilisation; per uvicorn worker CPU (cores, average and peak);
- PostgreSQL: transactions/s, mean active time per committed transaction, active backends,
  lock waiters and the oldest lock wait, idle-in-transaction backends;
- API: requests in flight on the server (from each request's start/end), and latency + SQL time per route;
- the smoke's desk actions, desk request volume and ghost throughput.
"""

import itertools
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def q(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))] if values else None


def load_jsonl(path):
    rows, dec = [], json.JSONDecoder()
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        i = line.find("{")
        while i != -1:
            try:
                obj, end = dec.raw_decode(line, i)
            except ValueError:
                i = line.find("{", i + 1)
                continue
            rows.append(obj)
            i = line.find("{", end)
    return rows


def observer(rows, start, end):
    meta = next((r["meta"] for r in rows if "meta" in r), {})
    samples = [r for r in rows if "t" in r and start <= r["t"] <= end]
    out = {
        "seconds": round(samples[-1]["t"] - samples[0]["t"]) if len(samples) > 1 else 0,
        "containers": {},
    }
    if len(samples) < 3:
        return out
    for name in samples[0]["c"]:
        cores, throttled, psi = [], [], []
        for a, b in itertools.pairwise(samples):
            dt = b["t"] - a["t"]
            ca, cb = a["c"][name], b["c"][name]
            cores.append((cb["usage_us"] - ca["usage_us"]) / 1e6 / dt)
            periods = cb["periods"] - ca["periods"]
            throttled.append(
                (cb["throttled"] - ca["throttled"]) / periods if periods else 0
            )
            psi.append((cb["psi_us"] - ca["psi_us"]) / 1e6 / dt)
        first, last = samples[0]["c"][name], samples[-1]["c"][name]
        periods = last["periods"] - first["periods"]
        out["containers"][name] = {
            "limit_cores": meta.get("limits", {}).get(name),
            "cpu_cores_avg": round(sum(cores) / len(cores), 3),
            "cpu_cores_p95": round(q(cores, 0.95), 3),
            "cpu_cores_max": round(max(cores), 3),
            "throttled_periods_pct": round(
                100 * (last["throttled"] - first["throttled"]) / periods, 1
            )
            if periods
            else 0,
            "throttled_seconds": round(
                (last["throttled_us"] - first["throttled_us"]) / 1e6, 1
            ),
            "throttled_pct_p95_1s": round(100 * q(throttled, 0.95), 1),
            "cpu_pressure_some_pct": round(100 * sum(psi) / len(psi), 1),
            "cpu_pressure_some_p95_pct": round(100 * q(psi, 0.95), 1),
            "mem_peak_mib": round(max(s["c"][name]["mem"] for s in samples) / 2**20),
        }
    host = []
    for a, b in itertools.pairwise(samples):
        ja, jb = a["host"]["jiffies"], b["host"]["jiffies"]
        total = sum(jb) - sum(ja)
        idle = (jb[3] + jb[4]) - (ja[3] + ja[4])
        host.append(1 - idle / total if total else 0)
    out["host_cpu_pct_avg"] = round(100 * sum(host) / len(host), 1)
    out["host_cpu_pct_max"] = round(100 * max(host), 1)
    tick = meta.get("clk_tck", 100)
    workers = {}
    for a, b in itertools.pairwise(samples):
        dt = b["t"] - a["t"]
        for pid, proc in b.get("api_procs", {}).items():
            if "spawn_main" not in proc["cmd"] or pid not in a.get("api_procs", {}):
                continue
            workers.setdefault(pid, []).append(
                (proc["cpu_ticks"] - a["api_procs"][pid]["cpu_ticks"]) / tick / dt
            )
    out["api_workers"] = {
        pid: {
            "cores_avg": round(sum(v) / len(v), 3),
            "cores_p95": round(q(v, 0.95), 3),
            "cores_max": round(max(v), 3),
        }
        for pid, v in workers.items()
    }
    pg = [s["pg"] for s in samples if "pg" in s]
    if len(pg) > 1:
        commits = pg[-1]["xact_commit"] - pg[0]["xact_commit"]
        rollbacks = pg[-1]["xact_rollback"] - pg[0]["xact_rollback"]
        active = pg[-1]["active_time_ms"] - pg[0]["active_time_ms"]
        out["postgres"] = {
            "commits_per_s": round(commits / out["seconds"], 1),
            "rollbacks_per_s": round(rollbacks / out["seconds"], 1),
            "active_ms_per_commit": round(active / commits, 2) if commits else None,
            "active_ms_per_transaction": round(active / (commits + rollbacks), 2)
            if commits + rollbacks
            else None,
            "active_backends_avg": round(sum(p["active"] for p in pg) / len(pg), 2),
            "active_backends_max": max(p["active"] for p in pg),
            "idle_in_tx_max": max(p["idle_tx"] for p in pg),
            "lock_waiters_max": max(p["lock_waiters"] for p in pg),
            "oldest_lock_wait_ms_max": max(p["oldest_lock_ms"] for p in pg),
            "deadlocks": pg[-1]["deadlocks"] - pg[0]["deadlocks"],
        }
    return out


def requests(rows, start, end):
    reqs = []
    for r in rows:
        if (
            r.get("event") != "request"
            or r["path"] in ("/api/health", "/api/ready")
            or "mediamtx" in r["path"]
        ):
            continue
        stop = datetime.fromisoformat(r["timestamp"]).timestamp()
        if not start <= stop <= end:
            continue
        r["_end"], r["_start"] = stop, stop - r["duration_ms"] / 1000
        reqs.append(r)
    out = {"count": len(reqs)}
    if not reqs:
        return out
    # Requests in flight on the server, sampled every 50 ms over the busy span.
    events = sorted([(r["_start"], 1) for r in reqs] + [(r["_end"], -1) for r in reqs])
    level, i, inflight = 0, 0, []
    t = events[0][0]
    while t <= events[-1][0]:
        while i < len(events) and events[i][0] <= t:
            level += events[i][1]
            i += 1
        inflight.append(level)
        t += 0.05
    busy = [x for x in inflight if x > 0]
    out["in_flight"] = {
        "max": max(inflight),
        "p95_while_busy": q(busy, 0.95),
        "mean_while_busy": round(sum(busy) / len(busy), 2) if busy else 0,
    }
    routes = {}
    for r in reqs:
        key = (
            r["method"]
            + " "
            + re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", ":id", r["path"]).replace(
                "/api/v1", ""
            )
        )
        routes.setdefault(key, []).append(r)
    out["routes"] = {
        k: {
            "n": len(v),
            "p50": round(q([r["duration_ms"] for r in v], 0.5)),
            "p95": round(q([r["duration_ms"] for r in v], 0.95)),
            "db_p50": round(q([r.get("db_ms", 0) for r in v], 0.5)),
            "db_p95": round(q([r.get("db_ms", 0) for r in v], 0.95)),
            "queries_p50": q([r.get("db_queries", 0) for r in v], 0.5),
            "ms_per_query_p50": round(
                q([r["db_ms"] / r["db_queries"] for r in v if r.get("db_queries")], 0.5)
                or 0,
                2,
            ),
        }
        for k, v in sorted(routes.items(), key=lambda kv: -len(kv[1]))
    }
    return out


def main():
    for run in map(Path, sys.argv[1:]):
        window = dict(
            line.split(" ", 1) for line in (run / "window.txt").read_text().splitlines()
        )
        start = datetime.fromisoformat(window["start"] + "+00:00").timestamp()
        end = datetime.fromisoformat(window["end"].split(" ")[0] + "+00:00").timestamp()
        result = (
            json.loads((run / "result.json").read_text())
            if (run / "result.json").exists()
            else {}
        )
        summary = {
            "run": run.name,
            "passed": result.get("passed"),
            "failures": result.get("failures"),
            "actions": {
                k: {x: v[x] for x in ("count", "median", "p95", "max") if x in v}
                for k, v in (result.get("actions") or {}).items()
            },
            "desk": {
                k: v for k, v in (result.get("desk") or {}).items() if k != "byRoute"
            },
            "desk_routes": (result.get("desk") or {}).get("byRoute"),
            "drains": result.get("drains"),
            "bursts": result.get("bursts"),
            "server": observer(load_jsonl(run / "observer.jsonl"), start, end),
            "api": requests(load_jsonl(run / "api.jsonl"), start, end + 5),
        }
        (run / "summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
