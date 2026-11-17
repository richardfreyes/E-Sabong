#!/usr/bin/env python3
"""Read-only resource observer for the shared UAT/production host (runs on the host, stdlib only).

Samples once per second until the duration ends (or SIGTERM), one JSON line per sample:
- cgroup v2 CPU usage, CFS periods/throttling, CPU pressure and memory for the named containers;
- host CPU (/proc/stat) and host CPU pressure;
- CPU time of every process in the UAT API container (the uvicorn master and its workers);
- PostgreSQL activity of the UAT database through ONE long-lived psql session (no per-sample
  connections): backends by state, lock waiters and the oldest lock wait, idle-in-transaction,
  commits/rollbacks and pg_stat_database.active_time (transaction latency = d(active_time)/d(xacts)).

Nothing is written to any container or database. Usage:
  uat_observe.py OUT.jsonl SECONDS [container ...]
"""

import json
import os
import signal
import subprocess
import sys
import time

DEFAULT = [
    "esabong-uat-api-1",
    "esabong-uat-postgres-1",
    "esabong-uat-worker-1",
    "esabong-prod-api-1",
    "esabong-prod-postgres-1",
    "esabong-uat-redis-1",
    "esabong-prod-redis-1",
]
API = "esabong-uat-api-1"
PG = "esabong-uat-postgres-1"
PG_QUERY = (
    "SELECT json_build_object("
    "'backends', (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()),"
    "'active', (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'active' AND pid <> pg_backend_pid()),"
    "'idle_tx', (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state LIKE 'idle in transaction%'),"
    "'lock_waiters', (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'),"
    "'oldest_lock_ms', (SELECT coalesce(max(extract(epoch FROM clock_timestamp() - state_change) * 1000), 0)::int"
    "  FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'),"
    "'xact_commit', d.xact_commit, 'xact_rollback', d.xact_rollback, 'active_time_ms', d.active_time,"
    "'deadlocks', d.deadlocks, 'blks_read', d.blks_read, 'tup_returned', d.tup_returned)"
    " FROM pg_stat_database d WHERE d.datname = current_database();\n"
)


def sh(*args: str) -> str:
    return subprocess.run(
        args, capture_output=True, text=True, check=True
    ).stdout.strip()


def read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def kv(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = int(parts[1])
    return out


def pressure(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("some"):
            return int(line.rsplit("total=", 1)[1])
    return 0


def main() -> None:
    out_path, seconds = sys.argv[1], float(sys.argv[2])
    names = sys.argv[3:] or DEFAULT
    cgroups = {}
    for name in names:
        cid = sh("docker", "inspect", "-f", "{{.Id}}", name)
        quota = sh("docker", "inspect", "-f", "{{.HostConfig.NanoCpus}}", name)
        cgroups[name] = (
            f"/sys/fs/cgroup/system.slice/docker-{cid}.scope",
            int(quota) / 1e9,
        )
    psql = subprocess.Popen(
        [
            "docker",
            "exec",
            "-i",
            PG,
            "sh",
            "-c",
            'exec psql -X -q -At -U "$POSTGRES_USER" -d esabong',
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    tick = os.sysconf("SC_CLK_TCK")
    end = time.time() + seconds
    with open(out_path, "a") as out:
        out.write(
            json.dumps(
                {
                    "meta": {
                        "limits": {n: c[1] for n, c in cgroups.items()},
                        "clk_tck": tick,
                    }
                }
            )
            + "\n"
        )
        while time.time() < end and not stop:
            began = time.time()
            sample = {"t": round(began, 3), "c": {}}
            for name, (base, _) in cgroups.items():
                cpu = kv(read(base + "/cpu.stat"))
                sample["c"][name] = {
                    "usage_us": cpu.get("usage_usec", 0),
                    "periods": cpu.get("nr_periods", 0),
                    "throttled": cpu.get("nr_throttled", 0),
                    "throttled_us": cpu.get("throttled_usec", 0),
                    "psi_us": pressure(read(base + "/cpu.pressure")),
                    "mem": int(read(base + "/memory.current") or 0),
                }
            host = read("/proc/stat").splitlines()[0].split()[1:]
            sample["host"] = {
                "jiffies": [int(x) for x in host[:8]],
                "psi_us": pressure(read("/proc/pressure/cpu")),
            }
            procs = {}
            for pid in read(cgroups[API][0] + "/cgroup.procs").split():
                stat = read(f"/proc/{pid}/stat")
                if not stat:
                    continue
                fields = stat.rsplit(")", 1)[1].split()
                cmd = read(f"/proc/{pid}/cmdline").replace("\0", " ")
                procs[pid] = {
                    "cpu_ticks": int(fields[11]) + int(fields[12]),
                    "cmd": cmd[:80],
                }
            sample["api_procs"] = procs
            try:
                psql.stdin.write(PG_QUERY)
                psql.stdin.flush()
                sample["pg"] = json.loads(psql.stdout.readline())
            # Keep sampling the host even if the database session drops.
            except (OSError, ValueError) as error:
                sample["pg_error"] = str(error)[:120]
            out.write(json.dumps(sample, separators=(",", ":")) + "\n")
            out.flush()
            time.sleep(max(0.0, 1.0 - (time.time() - began)))
    psql.terminate()


if __name__ == "__main__":
    main()
