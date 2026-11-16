"""Declarator ghost-bet concurrency benchmark against a real API (local isolated stacks only).

Drives the HTTP API the way the desk does: concurrent manual ghost clicks with one idempotency key
each, auto ghost betting, Stop and Close during bursts, Declare right after Close, two Declarator
sessions, background player bets and desk reads, and two tenants at once. Every scenario checks
the accounting against the server: accepted clicks == the book's dummy delta == receipts in the
database, nothing changes after Close, refusals are 409 ``betting_closed``, reconciliation stays
balanced, and no tenant's sockets receive another tenant's fights.

Lock waits are sampled from pg_stat_activity every 20 ms; container CPU throttling from cgroup
cpu.stat. Output: one JSON document with raw latencies and summaries.

    api/.venv/bin/python scripts/benchmarks/declarator_concurrency.py \
        --base http://127.0.0.1:8112 --state <runtime.json> --container esb-conc-after --out after.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import re
import subprocess
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import websockets

PASSWORD = "DemoPass#2026"  # noqa: S105 - public development default of the isolated fixture


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": pct(values, 0.5),
        "p95": pct(values, 0.95),
        "max": pct(values, 1.0),
    }


class Actor:
    def __init__(self, base: str, host: str, username: str, portal: str):
        self.username = username
        self.host = host
        self.client = httpx.AsyncClient(
            base_url=base,
            headers={
                "Host": host,
                "X-Requested-With": "XMLHttpRequest",
                "X-ESB-Client": "2",
                "X-Portal": portal,
            },
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=128),
            timeout=60,
        )
        self.token = ""
        self.id = ""

    async def login(self) -> Actor:
        r = await self.client.post("/api/v1/auth/login", json={"username": self.username, "password": PASSWORD})
        r.raise_for_status()
        self.token = r.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        self.id = (await self.client.get("/api/v1/auth/me")).json()["id"]
        return self

    async def call(self, method: str, path: str, body=None) -> tuple[int, dict, float]:
        began = time.perf_counter()
        r = await self.client.request(method, "/api/v1" + path, json=body)
        ms = (time.perf_counter() - began) * 1000
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 401:  # access token expired mid-run
            await self.login()
            return await self.call(method, path, body)
        return r.status_code, data, ms


class LockSampler:
    """pg_stat_activity every 20 ms: backends waiting on a lock, and the oldest such wait."""

    def __init__(self, container: str, database: str):
        self.container, self.database = container, database
        self.samples: list[tuple[float, int, float]] = []
        self.task: asyncio.Task | None = None

    async def _run(self) -> None:
        # Other roles' wait events are visible only to a superuser (or pg_read_all_stats), so this reads
        # them through a persistent psql inside the local PostgreSQL container instead of granting a role.
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            self.container,
            "psql",
            "-U",
            "postgres",
            "-At",
            "-q",
            "-F",
            ",",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        query = (
            "SELECT count(*) FILTER (WHERE wait_event_type = 'Lock'),"  # noqa: S608 - name validated at startup
            " coalesce(round(extract(epoch FROM max(now() - state_change) FILTER (WHERE wait_event_type = 'Lock')) * 1000), 0),"
            # The database name was checked against esabong_test_declarator_[a-z0-9_]+ at startup.
            f" count(*) FROM pg_stat_activity WHERE datname = '{self.database}' AND pid <> pg_backend_pid();\n"
        ).encode()
        try:
            while True:
                proc.stdin.write(query)
                await proc.stdin.drain()
                waiting, oldest, conns = (await proc.stdout.readline()).decode().strip().split(",")
                self.samples.append((time.perf_counter(), int(waiting), float(oldest), int(conns)))
                await asyncio.sleep(0.02)
        finally:
            proc.kill()

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    def window(self, start: float, end: float) -> dict:
        rows = [s for s in self.samples if start <= s[0] <= end]
        return {
            "samples": len(rows),
            "peak_lock_waiters": max((r[1] for r in rows), default=0),
            "samples_with_lock_wait": sum(1 for r in rows if r[1]),
            "oldest_lock_wait_ms": round(max((r[2] for r in rows), default=0.0), 1),
            "peak_connections": max((r[3] for r in rows), default=0),
        }


def cpu_stat(container: str | None) -> dict:
    if not container:
        return {}
    out = subprocess.run(  # noqa: S603 - local benchmark container named by the operator
        ["docker", "exec", container, "cat", "/sys/fs/cgroup/cpu.stat"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return {k: int(v) for k, v in (line.split() for line in out.splitlines() if line.strip())}


def cpu_delta(a: dict, b: dict, seconds: float) -> dict:
    if not a or not b:
        return {}
    periods = b["nr_periods"] - a["nr_periods"]
    return {
        "cpu_seconds": round((b["usage_usec"] - a["usage_usec"]) / 1e6, 2),
        "cpu_util": round((b["usage_usec"] - a["usage_usec"]) / 1e6 / seconds, 2) if seconds else None,
        "throttled_periods_pct": round(100 * (b["nr_throttled"] - a["nr_throttled"]) / periods, 1) if periods else 0,
    }


class Tenant:
    """One tenant's fixture: admin, Declarator sessions, players, and a live event."""

    def __init__(self, base: str, slug: str, base_domain: str):
        self.base, self.slug = base, slug
        self.host = f"{slug}.{base_domain}"
        self.ws_frames: list[dict] = []
        self.match_id = ""
        self.stop_load = asyncio.Event()
        self.load_stats = {
            "bets_ok": 0,
            "bets_refused": 0,
            "bets_error": 0,
            "reads": 0,
            "read_ms": [],
        }

    async def setup(self, players: int, run: str) -> None:
        self.admin = await Actor(self.base, self.host, "admin", "admin").login()
        await self.admin.call(
            "PUT",
            "/admin/betting/settings",
            {
                "enabled": True,
                "starting_balance": "10000000.00",
                "min_bet": "1.00",
                "max_fight_stake": "10000000.00",
            },
        )
        self.desk = await Actor(self.base, self.host, "declarator", "admin").login()
        self.desk2 = await Actor(self.base, self.host, "declarator", "admin").login()  # a second Declarator session
        status, event, _ = await self.admin.call(
            "POST",
            "/admin/events",
            {
                "name": f"Concurrency {run}",
                "venue": "bench",
                "starts_at": "2026-11-17T00:00:00Z",
            },
        )
        assert status == 201, event
        self.event_id = event["id"]
        for op in ("publish", "start"):
            status, body, _ = await self.admin.call("POST", f"/admin/events/{self.event_id}/{op}")
            assert status == 200, body
        status, body, _ = await self.admin.call("PUT", f"/declarator/events/{self.event_id}/assignments/{self.desk.id}")
        assert status == 200, body
        self.players: list[Actor] = []
        for i in range(players):
            name = f"bench-{run}-{i}"
            status, body, _ = await self.admin.call(
                "POST",
                "/admin/users",
                {
                    "username": name,
                    "full_name": f"Bench {i}",
                    "account_type": "player",
                    "password": PASSWORD,
                },
            )
            assert status == 201, body
            self.players.append(await Actor(self.base, self.host, name, "player").login())

    async def listen(self, actor: Actor) -> None:
        url = self.base.replace("http", "ws") + "/api/ws?token=" + actor.token
        with contextlib.suppress(Exception):
            async with websockets.connect(url, additional_headers={"Host": self.host}) as ws:
                while not self.stop_load.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 1)
                    except TimeoutError:
                        continue
                    self.ws_frames.append(json.loads(raw))

    async def open_fight(self) -> str:
        status, body, ms = await self.desk.call("POST", f"/declarator/events/{self.event_id}/betting/open")
        assert status == 200 and body["status"] == "open", body
        self.match_id = body["match_id"]
        return self.match_id

    async def book(self) -> dict:
        status, body, _ = await self.desk.call("GET", f"/declarator/matches/{self.match_id}/book")
        assert status == 200, body
        return body

    async def player_load(self, actor: Actor, rate: float) -> None:
        rnd = random.Random(actor.username)  # noqa: S311 - reproducible load, not security
        while not self.stop_load.is_set():
            await asyncio.sleep(rnd.uniform(0.5, 1.5) / rate)
            if not self.match_id:
                continue
            body = {
                "side": rnd.choice(["meron", "wala"]),
                "amount": f"{rnd.randint(10, 500)}.00",
                "request_id": str(uuid.uuid4()),
            }
            status, data, _ = await actor.call("POST", f"/portal/matches/{self.match_id}/bets", body)
            key = "bets_ok" if status in (200, 201) else "bets_refused" if status in (409, 422) else "bets_error"
            self.load_stats[key] += 1
            if status not in (200, 201):
                code = f"{status}:{(data.get('error') or {}).get('code')}"
                codes = self.load_stats.setdefault("bet_codes", {})
                codes[code] = codes.get(code, 0) + 1

    async def desk_reader(self, interval: float) -> None:
        """A second desk's steady reads (book, online, ghost-auto), the same for every build measured."""
        reader = await Actor(self.base, self.host, "declarator", "admin").login()
        paths = [
            lambda: f"/declarator/matches/{self.match_id}/book",
            lambda: f"/declarator/events/{self.event_id}/online",
            lambda: f"/declarator/events/{self.event_id}/ghost-auto",
        ]
        i = 0
        while not self.stop_load.is_set():
            await asyncio.sleep(interval)
            if not self.match_id:
                continue
            _, _, ms = await reader.call("GET", paths[i % 3]())
            i += 1
            self.load_stats["reads"] += 1
            self.load_stats["read_ms"].append(ms)


class Bench:
    def __init__(self, args):
        self.args = args
        state = json.loads(Path(args.state).read_text())
        env = state["environment"]
        self.database = state["database"]
        # Receipts are read with the BYPASSRLS platform role (the owner is subject to FORCE RLS).
        self.dsn = env["DATABASE_URL_PLATFORM"].replace("postgresql+asyncpg", "postgresql")
        self.base_domain = env["BASE_DOMAIN"]
        self.sampler = LockSampler(args.pg_container, self.database)
        self.results: dict = {"label": args.label, "base": args.base, "scenarios": {}}
        self.errors: list[str] = []

    async def receipts(self, match_id: str) -> int:
        conn = await asyncpg.connect(self.dsn)
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM dummy_stake_receipts WHERE match_id = $1",
                uuid.UUID(match_id),
            )
        finally:
            await conn.close()

    async def click(self, t: Tenant, side: str, amount: int, actor: Actor | None = None) -> dict:
        actor = actor or t.desk
        rid = str(uuid.uuid4())
        sent = time.perf_counter()
        status, body, ms = await actor.call(
            "POST",
            f"/declarator/matches/{t.match_id}/dummy-bets",
            {"side": side, "amount": f"{amount}.00", "request_id": rid},
        )
        return {
            "rid": rid,
            "side": side,
            "amount": amount,
            "status": status,
            "code": (body.get("error") or {}).get("code") if status != 200 else None,
            "ms": ms,
            "sent": sent,
            "done": sent + ms / 1000,
        }

    def account(
        self,
        name: str,
        t: Tenant,
        before: dict,
        after: dict,
        clicks: list[dict],
        receipts_before: int,
        receipts_after: int,
    ) -> dict:
        accepted = [c for c in clicks if c["status"] == 200]
        refused = [c for c in clicks if c["status"] != 200]
        delta = {s: Decimal(after[f"dummy_{s}"]) - Decimal(before[f"dummy_{s}"]) for s in ("meron", "wala")}
        expect = {s: Decimal(sum(c["amount"] for c in accepted if c["side"] == s)) for s in ("meron", "wala")}
        ok = delta == expect and receipts_after - receipts_before == len(accepted)
        bad_refusals = [c for c in refused if not (c["status"] == 409 and c["code"] == "betting_closed")]
        if not ok:
            self.errors.append(
                f"{name}: book delta {delta} != accepted {expect}"
                f" or receipts {receipts_after - receipts_before} != {len(accepted)}"
            )
        if bad_refusals:
            self.errors.append(f"{name}: unexpected refusals {[(c['status'], c['code']) for c in bad_refusals][:5]}")
        return {
            "accepted": len(accepted),
            "refused": len(refused),
            "book_delta": {k: str(v) for k, v in delta.items()},
            "receipts_added": receipts_after - receipts_before,
            "consistent": ok and not bad_refusals,
        }

    async def burst(
        self,
        name: str,
        t: Tenant,
        sides: list[str],
        *,
        actor2_share: int = 0,
        interrupt=None,
        at_ms: float = 30,
    ) -> dict:
        before = await t.book()
        r0 = await self.receipts(t.match_id)
        c0 = cpu_stat(self.args.container)
        start = time.perf_counter()
        tasks = []
        for i, side in enumerate(sides):
            actor = t.desk2 if i < actor2_share else t.desk
            tasks.append(asyncio.create_task(self.click(t, side, 5000, actor)))
        interrupt_result = None
        if interrupt is not None:
            await asyncio.sleep(at_ms / 1000)
            began = time.perf_counter()
            status, body, ms = await interrupt()
            interrupt_result = {
                "status": status,
                "ms": round(ms, 1),
                "sent_after_ms": round((began - start) * 1000, 1),
            }
        clicks = await asyncio.gather(*tasks)
        end = time.perf_counter()
        c1 = cpu_stat(self.args.container)
        await asyncio.sleep(0.3)
        after = await t.book()
        r1 = await self.receipts(t.match_id)
        result = {
            "clicks": len(sides),
            "latency_ms": summary([c["ms"] for c in clicks if c["status"] == 200]),
            "all_latency_ms": [round(c["ms"], 1) for c in clicks],
            "wall_ms": round((end - start) * 1000, 1),
            "locks": self.sampler.window(start, end),
            "cpu": cpu_delta(c0, c1, end - start),
            **self.account(name, t, before, after, clicks, r0, r1),
        }
        if interrupt_result:
            result["interrupt"] = interrupt_result
        return result

    async def settle(self, t: Tenant, outcome: str = "meron") -> dict:
        status, body, close_ms = await t.desk.call("POST", f"/declarator/matches/{t.match_id}/betting/close")
        assert status == 200, body
        began = time.perf_counter()
        status, body, ms = await t.desk.call(
            "POST",
            f"/declarator/matches/{t.match_id}/declarations",
            {
                "request_id": str(uuid.uuid4()),
                "outcome": outcome,
                "base_revision": 0,
                "reason": None,
            },
        )
        if status != 200:
            self.errors.append(f"declare {status} {body}")
        return {
            "close_ms": round(close_ms, 1),
            "declare_ms": round(ms, 1),
            "declare_status": status,
            "declare_after_close_ms": round((time.perf_counter() - began) * 1000, 1),
        }

    async def cleanup(self, t: Tenant) -> None:
        """After a failed scenario: stop auto ghost betting and settle the open fight, so the next one starts clean."""
        await t.desk.call("POST", f"/declarator/events/{t.event_id}/ghost-auto/stop")
        if t.match_id:
            await t.desk.call("POST", f"/declarator/matches/{t.match_id}/betting/close")
            await t.desk.call(
                "POST",
                f"/declarator/matches/{t.match_id}/declarations",
                {
                    "request_id": str(uuid.uuid4()),
                    "outcome": "meron",
                    "base_revision": 0,
                    "reason": None,
                },
            )

    async def scenario(self, name: str, coro, *tenants: Tenant) -> None:
        began = time.perf_counter()
        try:
            self.results["scenarios"][name] = await coro
        except Exception as exc:  # noqa: BLE001 - keep running the rest; record the failure
            self.errors.append(f"{name}: {type(exc).__name__}: {exc}")
            self.results["scenarios"][name] = {"error": str(exc)}
            for t in tenants or (self.tenants[0],):
                with contextlib.suppress(Exception):
                    await self.cleanup(t)
        print(
            f"[{self.args.label}] {name} {round(time.perf_counter() - began, 1)}s",
            flush=True,
        )

    async def run(self) -> dict:
        self.sampler.start()
        run = uuid.uuid4().hex[:6]
        a = Tenant(self.args.base, "soak", self.base_domain)
        b = Tenant(self.args.base, "soak2", self.base_domain)
        self.tenants = (a, b)
        await a.setup(self.args.players, run)
        await b.setup(max(2, self.args.players // 2), run)
        listeners = [asyncio.create_task(t.listen(p)) for t in (a, b) for p in t.players[:3]]
        load = []
        if self.args.load:
            load += [asyncio.create_task(a.player_load(p, self.args.bet_rate)) for p in a.players]
            load += [asyncio.create_task(a.desk_reader(self.args.read_interval))]
        rounds = self.args.rounds
        c0, t0 = cpu_stat(self.args.container), time.perf_counter()

        async def one_click() -> dict:
            await a.open_fight()
            lat = []
            for _ in range(20):
                c = await self.click(a, "meron", 5000)
                lat.append(c["ms"])
                await asyncio.sleep(0.05)
            return {"latency_ms": summary(lat), **(await self.settle(a))}

        await self.scenario("1_single_click_x20", one_click())

        async def bursts(n: int) -> dict:
            out = []
            for _ in range(rounds):
                await a.open_fight()
                r = await self.burst(f"burst{n}", a, ["meron"] * n)
                r.update(await self.settle(a))
                out.append(r)
            return {
                "rounds": out,
                "latency_ms": summary([x for r in out for x in r["all_latency_ms"]]),
                "wall_ms": summary([r["wall_ms"] for r in out]),
                "consistent": all(r["consistent"] for r in out),
            }

        for n in (8, 16, 32):
            await self.scenario(f"burst_{n}", bursts(n))

        async def mixed() -> dict:
            await a.open_fight()
            r = await self.burst("mixed", a, ["meron", "wala"] * 16)
            r.update(await self.settle(a))
            return r

        await self.scenario("meron_wala_32", mixed())

        async def auto_and_manual(stop: bool) -> dict:
            out = []
            for _ in range(rounds):
                await a.open_fight()
                status, body, _ = await a.desk.call(
                    "POST",
                    f"/declarator/events/{a.event_id}/ghost-auto/start",
                    {
                        "meron_amount": "100.00",
                        "wala_amount": "100.00",
                        "interval_seconds": 1,
                    },
                )
                assert status == 200, body
                await asyncio.sleep(1.5)
                stopper = (lambda: a.desk.call("POST", f"/declarator/events/{a.event_id}/ghost-auto/stop")) if stop else None
                # Auto ticks also move the dummy pools, so the manual accounting uses receipts instead.
                await a.book()
                r0 = await self.receipts(a.match_id)
                start = time.perf_counter()
                tasks = [asyncio.create_task(self.click(a, s, 5000)) for s in ["meron", "wala"] * 16]
                interrupt = None
                if stopper:
                    await asyncio.sleep(0.03)
                    status, _, ms = await stopper()
                    interrupt = {"status": status, "ms": round(ms, 1)}
                clicks = await asyncio.gather(*tasks)
                end = time.perf_counter()
                if not stop:
                    await asyncio.sleep(0.5)
                    status, _, ms = await a.desk.call("POST", f"/declarator/events/{a.event_id}/ghost-auto/stop")
                    interrupt = {"stop_after_burst_ms": round(ms, 1)}
                status, job, _ = await a.desk.call("GET", f"/declarator/events/{a.event_id}/ghost-auto")
                after = await a.book()
                r1 = await self.receipts(a.match_id)
                accepted = [c for c in clicks if c["status"] == 200]
                manual = sum(c["amount"] for c in accepted)
                auto = Decimal(job.get("added_meron") or 0) + Decimal(job.get("added_wala") or 0)
                # Each round is a fresh fight, so the book's ghost total is the auto job's counters plus the manual clicks.
                full = Decimal(after["dummy_meron"]) + Decimal(after["dummy_wala"])
                consistent = full == auto + manual and len(accepted) == len(clicks)
                if not consistent:
                    self.errors.append(
                        f"auto_manual: book {full} != auto {auto} + manual {manual}; accepted {len(accepted)}/{len(clicks)}"
                    )
                r = {
                    "latency_ms": summary([c["ms"] for c in clicks]),
                    "wall_ms": round((end - start) * 1000, 1),
                    "locks": self.sampler.window(start, end),
                    "auto_runs": job.get("runs"),
                    "manual_accepted": len(accepted),
                    "book_total": str(full),
                    "auto_added": str(auto),
                    "receipts_added": r1 - r0,
                    "consistent": consistent,
                    "stop": interrupt,
                }
                r.update(await self.settle(a))
                out.append(r)
            return {
                "rounds": out,
                "latency_ms": summary([x for r in out for x in [r["latency_ms"]["p95"]] if x]),
                "stop_ms": summary([r["stop"].get("ms") or r["stop"].get("stop_after_burst_ms") for r in out]),
                "consistent": all(r["consistent"] for r in out),
            }

        await self.scenario("auto_plus_manual_32", auto_and_manual(False))
        await self.scenario("stop_during_burst_32", auto_and_manual(True))

        async def close_during(n: int, at_ms: float) -> dict:
            out = []
            for _ in range(rounds):
                await a.open_fight()
                r = await self.burst(
                    f"close{n}",
                    a,
                    ["meron", "wala"] * (n // 2),
                    interrupt=lambda: a.desk.call("POST", f"/declarator/matches/{a.match_id}/betting/close"),
                    at_ms=at_ms,
                )
                frozen = await a.book()
                await asyncio.sleep(1.0)
                later = await a.book()
                if (frozen["dummy_meron"], frozen["dummy_wala"]) != (
                    later["dummy_meron"],
                    later["dummy_wala"],
                ) or later["state"] != "closed":
                    self.errors.append("close: book changed after close")
                    r["changed_after_close"] = True
                odds = later["odds"]
                r["final_odds"] = [odds["meron_display"], odds["wala_display"]]
                status, body, ms = await a.desk.call(
                    "POST",
                    f"/declarator/matches/{a.match_id}/declarations",
                    {
                        "request_id": str(uuid.uuid4()),
                        "outcome": "wala",
                        "base_revision": 0,
                        "reason": None,
                    },
                )
                r["declare_ms"] = round(ms, 1)
                if status != 200:
                    self.errors.append(f"declare after close {status} {body}")
                settled = await a.book()
                r["settled_state"] = settled["state"]
                out.append(r)
            return {
                "rounds": out,
                "close_ms": summary([r["interrupt"]["ms"] for r in out]),
                "declare_ms": summary([r["declare_ms"] for r in out]),
                "accepted": [r["accepted"] for r in out],
                "refused": [r["refused"] for r in out],
                "consistent": all(r["consistent"] for r in out),
            }

        await self.scenario("close_during_burst_32", close_during(32, 30))
        await self.scenario("close_during_burst_32_late", close_during(32, 120))

        async def two_desks() -> dict:
            await a.open_fight()
            r = await self.burst("two_desks", a, ["meron", "wala"] * 16, actor2_share=16)
            r.update(await self.settle(a))
            return r

        await self.scenario("two_declarator_sessions_32", two_desks())

        async def tenants() -> dict:
            await a.open_fight()
            await b.open_fight()
            ra, rb = await asyncio.gather(
                self.burst("tenant_a", a, ["meron"] * 32),
                self.burst("tenant_b", b, ["wala"] * 32),
            )
            sa, sb = await asyncio.gather(self.settle(a), self.settle(b))
            return {"soak": {**ra, **sa}, "soak2": {**rb, **sb}}

        await self.scenario("two_tenants_32_each", tenants(), a, b)

        elapsed = time.perf_counter() - t0
        c1 = cpu_stat(self.args.container)
        a.stop_load.set()
        b.stop_load.set()
        for task in load + listeners:
            task.cancel()
        await asyncio.gather(*load, *listeners, return_exceptions=True)

        # Tenant isolation: no tenant's player sockets saw the other tenant's fights or events.
        a_ids, b_ids = {a.event_id}, {b.event_id}
        leaks = [f for f in b.ws_frames if (f.get("data") or {}).get("event_id") in a_ids] + [
            f for f in a.ws_frames if (f.get("data") or {}).get("event_id") in b_ids
        ]
        if leaks:
            self.errors.append(f"cross-tenant frames: {len(leaks)}")
        status, rec, _ = await a.admin.call("GET", "/admin/betting/reconciliation")
        status_b, rec_b, _ = await b.admin.call("GET", "/admin/betting/reconciliation")
        if not (rec.get("balanced") and rec_b.get("balanced")):
            self.errors.append(f"reconciliation not balanced: {rec.get('checks')} {rec_b.get('checks')}")
        self.results.update(
            {
                "total_cpu": cpu_delta(c0, c1, elapsed),
                "background_load": {k: (summary(v) if isinstance(v, list) else v) for k, v in a.load_stats.items()},
                "ws_frames": {"soak": len(a.ws_frames), "soak2": len(b.ws_frames)},
                "cross_tenant_frames": len(leaks),
                "reconciliation_balanced": [rec.get("balanced"), rec_b.get("balanced")],
                "errors": self.errors,
            }
        )
        self.sampler.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.sampler.task
        return self.results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--container")
    p.add_argument("--pg-container", default="esabong-postgres-1")
    p.add_argument("--label", default="run")
    p.add_argument("--out", required=True)
    p.add_argument("--players", type=int, default=6)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--load", action="store_true", help="background player bets and desk reads")
    p.add_argument("--bet-rate", type=float, default=1.0, help="bets per second per player")
    p.add_argument("--read-interval", type=float, default=0.5)
    args = p.parse_args()
    if urlsplit(args.base).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Local isolated stacks only")
    if not re.fullmatch(
        r"esabong_test_declarator_[a-z0-9_]+",
        json.loads(Path(args.state).read_text())["database"],
    ):
        raise SystemExit("Isolated Declarator database required")
    result = asyncio.run(Bench(args).run())
    Path(args.out).write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({"errors": result["errors"], "out": args.out}))


if __name__ == "__main__":
    main()
