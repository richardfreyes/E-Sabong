"""Isolated fixture, read-only monitor and independent accounting oracle for benchmarks.

Run from api/ using its venv. Private state (URLs/JWTs) stays outside the repository.
No commands drop a database, reset shared statistics, or flush Redis.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def require_local(url: str) -> None:
    if urlsplit(url).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Benchmark database and Redis must use loopback hosts")


def load(state: Path) -> dict:
    values = json.loads((state / "runtime.json").read_text())
    if not re.fullmatch(r"esabong_bench_[a-z0-9_]+", values["database"]):
        raise ValueError("Only isolated esabong_bench_* databases are allowed")
    for key in ("DATABASE_URL_APP", "DATABASE_URL_PLATFORM", "DATABASE_URL_MIGRATIONS", "REDIS_URL"):
        require_local(values["environment"][key])
    os.environ.update(values["environment"])
    from app.core.config import get_settings

    get_settings.cache_clear()
    return values


async def prepare(args) -> None:
    import asyncpg
    from dotenv import dotenv_values
    from sqlalchemy import insert, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.core.config import get_settings
    from app.core.permissions import SYSTEM_ROLES
    from app.core.security import create_access_token, hash_password
    from app.models import Base  # noqa: F401
    from app.modules.audit.models import AuditLog
    from app.modules.betting.models import BettingSettings, PlayWallet, PlayWalletEntry
    from app.modules.simulations.models import SimulationArena
    from app.modules.subscriptions.models import SubscriptionPlan, TenantSubscription
    from app.modules.tenants.models import (
        ALL_MODULES,
        Tenant,
        TenantBranding,
        TenantSettings,
    )
    from app.modules.users.models import Role, RolePermission, TenantUser, UserRole

    if not re.fullmatch(r"esabong_bench_[a-z0-9_]+", args.database):
        raise ValueError("Dedicated benchmark database name required")
    if (args.state / "runtime.json").exists():
        raise ValueError("State directory already exists; use a fresh run ID")
    s = get_settings()
    root_env = dotenv_values(ROOT / ".env")
    dburl = make_url(s.database_url_migrations)
    for value in (s.database_url_app, s.database_url_platform, s.database_url_migrations, s.redis_url):
        require_local(value)
    password = root_env.get("POSTGRES_SUPERUSER_PASSWORD")
    conn = await asyncpg.connect(
        host=dburl.host,
        port=dburl.port or 5432,
        user=root_env.get("POSTGRES_SUPERUSER", "postgres"),
        password=password,
        database="postgres",
    )
    try:
        await conn.execute(f'CREATE DATABASE "{args.database}" OWNER esabong_owner')
        await conn.execute(f'REVOKE ALL ON DATABASE "{args.database}" FROM PUBLIC')
        await conn.execute(f'GRANT CONNECT ON DATABASE "{args.database}" TO esabong_app, esabong_platform')
    finally:
        await conn.close()
    env = {
        key.upper(): make_url(getattr(s, key)).set(database=args.database).render_as_string(hide_password=False)
        for key in (
            "database_url_app",
            "database_url_platform",
            "database_url_migrations",
        )
    }
    env.update(
        ENVIRONMENT="development",
        ALLOW_TENANT_HEADER="true",
        COOKIE_SECURE="false",
        PUBLIC_SCHEME="http",
        LOG_LEVEL="WARNING",
        LOG_JSON="false",
        JWT_SECRET=secrets.token_urlsafe(48),
        ACCESS_TOKEN_TTL_SECONDS="7200",  # noqa: S106 -- duration, not a credential
        DB_POOL_SIZE="8",
        DB_MAX_OVERFLOW="8",
        REDIS_URL=s.redis_url.rsplit("/", 1)[0] + "/12",
        MEDIA_ROOT=str(args.state / "media"),
    )
    env["ALEMBIC_DATABASE_URL"] = env["DATABASE_URL_MIGRATIONS"]
    values = {
        "database": args.database,
        "environment": env,
        "port": args.port,
        "workers": args.workers,
        "created_at": datetime.now(UTC).isoformat(),
        "fixture": str(args.state / "fixture.json"),
    }
    private_json(args.state / "runtime.json", values)
    os.environ.update(env)
    get_settings.cache_clear()
    conn = await asyncpg.connect(
        host=dburl.host,
        port=dburl.port or 5432,
        user=root_env.get("POSTGRES_SUPERUSER", "postgres"),
        password=password,
        database=args.database,
    )
    try:
        await conn.execute("ALTER SCHEMA public OWNER TO esabong_owner; REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await conn.execute("GRANT USAGE ON SCHEMA public TO esabong_app, esabong_platform")
    finally:
        await conn.close()
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "0015"],
        cwd=ROOT / "api",
        env={**os.environ, **env},
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Benchmark migration failed")
    engine = create_async_engine(env["DATABASE_URL_MIGRATIONS"])
    now = datetime.now(UTC)
    plan_id = uuid.uuid4()
    password_hash = hash_password(secrets.token_urlsafe(32))
    grant = Decimal("10000.00")
    tenants = []
    async with AsyncSession(engine, expire_on_commit=False) as db:
        plan = SubscriptionPlan(
            id=plan_id,
            code="synthetic-benchmark",
            name="Synthetic benchmark, not for sale",
            kind="saas",
            price_monthly_cents=0,
            price_annual_cents=0,
            currency="PHP",
            trial_days=0,
            limits={},
            modules=list(ALL_MODULES),
            is_active=False,
            prices_approved_at=now,
        )
        db.add(plan)
        await db.commit()
        for i in range(args.tenants):
            tid, admin_id, arena_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            slug = f"bench-{args.database.removeprefix('esabong_bench_').replace('_', '-')}-{i}"
            await db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
            db.add(
                Tenant(
                    id=tid,
                    slug=slug,
                    name=f"Synthetic benchmark tenant {i}",
                    status="active",
                    deployment_mode="shared",
                )
            )
            await db.flush()
            db.add_all(
                [
                    TenantSettings(tenant_id=tid, enabled_modules=list(ALL_MODULES)),
                    TenantBranding(tenant_id=tid, app_name=f"Benchmark {i}"),
                    TenantSubscription(
                        tenant_id=tid,
                        plan_id=plan_id,
                        billing_cycle="monthly",
                        status="active",
                        current_period_start=now,
                        current_period_end=now + timedelta(days=365),
                    ),
                    BettingSettings(tenant_id=tid, enabled=False, starting_balance=grant),
                ]
            )
            admin_role, player_role = uuid.uuid4(), uuid.uuid4()
            db.add_all(
                [
                    Role(
                        id=admin_role,
                        tenant_id=tid,
                        name="tenant_admin",
                        is_system=True,
                    ),
                    Role(id=player_role, tenant_id=tid, name="player", is_system=True),
                ]
            )
            await db.flush()
            db.add_all(
                [RolePermission(tenant_id=tid, role_id=admin_role, permission=str(p)) for p in SYSTEM_ROLES["tenant_admin"][1]]
            )
            count = max(args.players, args.burst, args.ws_clients // args.tenants) if i == 0 else args.ws_clients // args.tenants
            profiles = [{"id": uuid.uuid4(), "wallet": uuid.uuid4()} for _ in range(count)]
            users = [
                {
                    "id": admin_id,
                    "tenant_id": tid,
                    "username": "bench-admin",
                    "full_name": "Synthetic Admin",
                    "account_type": "admin",
                    "status": "active",
                    "must_change_password": False,
                    "password_hash": password_hash,
                }
            ]
            users += [
                {
                    "id": p["id"],
                    "tenant_id": tid,
                    "username": f"synthetic-{j}",
                    "full_name": f"Synthetic Player {j}",
                    "account_type": "player",
                    "status": "active",
                    "must_change_password": False,
                    "password_hash": password_hash,
                }
                for j, p in enumerate(profiles)
            ]
            await db.execute(insert(TenantUser), users)
            await db.execute(
                insert(UserRole),
                [{"tenant_id": tid, "user_id": admin_id, "role_id": admin_role}]
                + [{"tenant_id": tid, "user_id": p["id"], "role_id": player_role} for p in profiles],
            )
            await db.execute(
                insert(PlayWallet),
                [
                    {
                        "id": p["wallet"],
                        "tenant_id": tid,
                        "user_id": p["id"],
                        "balance": grant,
                        "version": 1,
                    }
                    for p in profiles
                ],
            )
            await db.execute(
                insert(PlayWalletEntry),
                [
                    {
                        "tenant_id": tid,
                        "wallet_id": p["wallet"],
                        "kind": "grant",
                        "delta": grant,
                        "balance_after": grant,
                        "idempotency_key": f"grant:{p['id']}",
                        "memo": "Synthetic staging system allocation; no monetary value",
                    }
                    for p in profiles
                ],
            )
            db.add(
                SimulationArena(
                    id=arena_id,
                    tenant_id=tid,
                    name=f"Synthetic Arena {i}",
                    enabled=True,
                )
            )
            db.add(
                AuditLog(
                    tenant_id=tid,
                    actor_type="system",
                    actor_label="benchmark fixture",
                    action="benchmark.fixture_created",
                    resource_type="tenant",
                    resource_id=str(tid),
                    changes={"players": count, "system_grant": str(grant)},
                )
            )
            await db.commit()

            def token(uid, tenant_id=tid):
                return create_access_token(
                    subject_id=uid,
                    audience="tenant",
                    tenant_id=tenant_id,
                    family=uuid.uuid4(),
                )[0]

            tenants.append(
                {
                    "id": str(tid),
                    "slug": slug,
                    "arenaId": str(arena_id),
                    "admin": {"id": str(admin_id), "token": token(admin_id)},
                    "players": [{"id": str(p["id"]), "token": token(p["id"])} for p in profiles],
                }
            )
            print(json.dumps({"fixture_tenant": i, "players": count}), flush=True)
    await engine.dispose()
    private_json(
        args.state / "fixture.json",
        {
            "base": f"http://127.0.0.1:{args.port}",
            "runId": args.database,
            "tenants": tenants,
        },
    )
    print(
        json.dumps(
            {
                "prepared": args.database,
                "tenants": len(tenants),
                "players": sum(len(t["players"]) for t in tenants),
            }
        )
    )


def cents(value) -> int:
    return int(Decimal(str(value)) * 100)


async def audit(args) -> None:
    values = load(args.state)
    import asyncpg
    from sqlalchemy.engine import make_url

    url = make_url(values["environment"]["DATABASE_URL_PLATFORM"])
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        server_settings={"default_transaction_read_only": "on"},
    )
    stakes = await conn.fetch("SELECT * FROM simulation_stakes ORDER BY created_at, id")
    receipts = await conn.fetch("SELECT * FROM simulation_settlements ORDER BY created_at, id")
    wallets = await conn.fetch("SELECT id, user_id, balance FROM play_wallets ORDER BY id")
    ledger = await conn.fetch("SELECT wallet_id, kind, delta, idempotency_key, balance_after FROM play_wallet_entries")
    matches = {str(r["id"]): dict(r) for r in await conn.fetch("SELECT * FROM simulation_matches")}
    await conn.close()
    grouped = defaultdict(list)
    for s in stakes:
        grouped[str(s["match_id"])].append(s)
    expected = defaultdict(int)
    actual_ledger = defaultdict(int)
    keys = defaultdict(list)
    for e in ledger:
        actual_ledger[str(e["wallet_id"])] += cents(e["delta"])
        keys[e["idempotency_key"]].append(e)
        if e["kind"] == "grant":
            expected[str(e["wallet_id"])] += cents(e["delta"])
    user_wallet = {str(w["user_id"]): str(w["id"]) for w in wallets}
    mismatch = []
    for s in stakes:
        expected[user_wallet[str(s["player_id"])]] -= cents(s["amount"])
        posted = keys[f"simulation:stake:{s['id']}"]
        if len(posted) != 1 or cents(posted[0]["delta"]) != -cents(s["amount"]):
            mismatch.append({"kind": "stake_debit", "stake_id": str(s["id"])})
    summaries = []
    detail_file = args.out / f"{args.label}-accounting.jsonl"
    args.out.mkdir(parents=True, exist_ok=True)
    with detail_file.open("w") as details:
        for receipt in receipts:
            mid = str(receipt["match_id"])
            lines = grouped[mid]
            totals = {side: sum(cents(s["amount"]) for s in lines if s["side"] == side) for side in ("team_a", "team_b")}
            total = sum(totals.values())
            refund = receipt["outcome"] in ("draw", "cancelled") or not all(totals.values())
            rate = Decimal(matches[mid]["simulation_fee_rate"])
            fee = 0 if refund else int((Decimal(total) * rate).quantize(Decimal(1), rounding="ROUND_HALF_UP"))
            awards = {str(s["id"]): cents(s["amount"]) if refund else 0 for s in lines}
            if not refund:
                winners = [s for s in lines if s["side"] == receipt["outcome"]]
                denominator = totals[receipt["outcome"]]
                weights = [(total - fee) * cents(s["amount"]) for s in winners]
                floor = [weight // denominator for weight in weights]
                order = sorted(range(len(winners)), key=lambda i: (-(weights[i] % denominator), i))
                for i in order[: total - fee - sum(floor)]:
                    floor[i] += 1
                awards.update({str(s["id"]): floor[i] for i, s in enumerate(winners)})
            receipt_lines = json.loads(receipt["lines"]) if isinstance(receipt["lines"], str) else receipt["lines"]
            local_mismatch = 0
            for s in lines:
                award = awards[str(s["id"])]
                expected[user_wallet[str(s["player_id"])]] += award
                recorded = receipt_lines.get(str(s["id"]), {})
                ok = cents(recorded.get("amount", "-1")) == award
                posted = keys[f"simulation:settle:{mid}:{s['id']}"]
                ok = ok and ((len(posted) == 1 and cents(posted[0]["delta"]) == award) if award else len(posted) == 0)
                if not ok:
                    local_mismatch += 1
                    mismatch.append({"kind": "settlement_line", "stake_id": str(s["id"])})
                details.write(
                    json.dumps(
                        {
                            "match_id": mid,
                            "stake_id": str(s["id"]),
                            "player_id": str(s["player_id"]),
                            "stake_cents": cents(s["amount"]),
                            "expected_award_cents": award,
                            "actual_award_cents": cents(recorded.get("amount", "-1")),
                            "ok": ok,
                        }
                    )
                    + "\n"
                )
            summary = {
                "match_id": mid,
                "outcome": receipt["outcome"],
                "stakes": len(lines),
                "distinct_players": len({s["player_id"] for s in lines}),
                "total_stake_cents": str(total),
                "fee_cents": str(fee),
                "net_cents": str(total - fee),
                "award_cents": str(sum(awards.values())),
                "rounding_remainder_cents": str(total - fee - sum(awards.values())),
                "line_mismatches": local_mismatch,
                "receipt_matches": cents(receipt["total_staked"]) == total
                and cents(receipt["fee_amount"]) == fee
                and cents(receipt["distributed"]) == total - fee,
            }
            summaries.append(summary)
    wallet_errors = [
        {
            "wallet_id": str(w["id"]),
            "stored_cents": str(cents(w["balance"])),
            "expected_cents": str(expected[str(w["id"])]),
        }
        for w in wallets
        if cents(w["balance"]) != expected[str(w["id"])] or cents(w["balance"]) != actual_ledger[str(w["id"])]
    ]
    result = {
        "label": args.label,
        "wallets": len(wallets),
        "stakes": len(stakes),
        "receipts": len(receipts),
        "settlements": summaries,
        "wallet_mismatches": len(wallet_errors),
        "line_mismatches": len(mismatch),
        "errors": (wallet_errors + mismatch)[:20],
        "accounting_log": str(detail_file),
        "accounting_sha256": hashlib.sha256(detail_file.read_bytes()).hexdigest(),
        "pass": not wallet_errors
        and not mismatch
        and all(s["receipt_matches"] and s["rounding_remainder_cents"] == "0" for s in summaries),
    }
    (args.out / f"{args.label}-audit.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


async def probe(args) -> None:
    values = load(args.state)
    import asyncpg
    from sqlalchemy.engine import make_url

    mid = uuid.UUID(args.match)
    url = make_url(values["environment"]["DATABASE_URL_PLATFORM"])
    read = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        server_settings={"default_transaction_read_only": "on"},
    )
    match = await read.fetchrow("SELECT tenant_id,state,version FROM simulation_matches WHERE id=$1", mid)
    count = await read.fetchval("SELECT count(*) FROM simulation_stakes WHERE match_id=$1", mid)
    await read.close()
    if match is None:
        raise ValueError("Unknown benchmark match")
    app = make_url(values["environment"]["DATABASE_URL_APP"])
    conn = await asyncpg.connect(
        host=app.host,
        port=app.port or 5432,
        user=app.username,
        password=app.password,
        database=app.database,
    )
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("SELECT set_config('app.tenant_id',$1,true)", str(match["tenant_id"]))
        uid = await conn.fetchval(
            "SELECT user_id FROM play_wallets WHERE tenant_id=$1 LIMIT 1",
            match["tenant_id"],
        )
        updated = await conn.execute(
            "UPDATE simulation_matches SET team_a_pool=team_a_pool WHERE id=$1 AND state='wagering_open'",
            mid,
        )
        inserted = await conn.execute(
            """INSERT INTO simulation_stakes
            (id,tenant_id,match_id,player_id,request_id,side,amount)
            SELECT $1,$2,$3,$4,$5,'team_a',0.01
            WHERE EXISTS (SELECT 1 FROM simulation_matches WHERE id=$3 AND state='wagering_open')""",
            uuid.uuid4(),
            match["tenant_id"],
            mid,
            uid,
            uuid.uuid4(),
        )
    finally:
        await tx.rollback()
        await conn.close()
    print(
        json.dumps(
            {
                "state": match["state"],
                "version": match["version"],
                "stakes": count,
                "update_rows": int(updated.split()[-1]),
                "insert_rows": int(inserted.split()[-1]),
                "probe_rolled_back": True,
            }
        )
    )


async def monitor(args) -> None:
    values = load(args.state)
    import asyncpg
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url

    url = make_url(values["environment"]["DATABASE_URL_MIGRATIONS"])
    root_env = dotenv_values(ROOT / ".env")
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=root_env.get("POSTGRES_SUPERUSER", "postgres"),
        password=root_env.get("POSTGRES_SUPERUSER_PASSWORD"),
        database=values["database"],
        server_settings={
            "default_transaction_read_only": "on",
            "application_name": "benchmark-monitor",
        },
    )
    pid = int((args.state / "api.pid").read_text())
    sql = """SELECT json_build_object(
      'connections', count(*), 'active', count(*) FILTER (WHERE state = 'active'),
      'lock_waiting', count(*) FILTER (WHERE wait_event_type = 'Lock'),
      'max_query_age_ms', coalesce(max(extract(epoch FROM clock_timestamp()-query_start)*1000) FILTER (WHERE state='active'),0),
      'wait_events', (SELECT json_object_agg(event,n) FROM (
           SELECT coalesce(wait_event_type,'CPU') || ':' || coalesce(wait_event,'running') event,
           count(*) n FROM pg_stat_activity
           WHERE datname=current_database() AND application_name != 'benchmark-monitor' GROUP BY 1) x),
      'waiting_locks', (SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
           WHERE a.datname=current_database() AND NOT l.granted)
      ) FROM pg_stat_activity WHERE datname=current_database() AND application_name != 'benchmark-monitor'"""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", buffering=1) as f:
        while True:
            sample = json.loads(await conn.fetchval(sql))
            stats = await conn.fetchrow(
                "SELECT xact_commit,xact_rollback,deadlocks,temp_bytes,blks_read,blks_hit "
                "FROM pg_stat_database WHERE datname=current_database()"
            )
            sample.update(dict(stats))
            proc = await asyncio.create_subprocess_exec("ps", "-axo", "pid=,ppid=,rss=,pcpu=", stdout=asyncio.subprocess.PIPE)
            output, _ = await proc.communicate()
            processes = []
            for line in output.decode().splitlines():
                fields = line.split()
                if len(fields) == 4:
                    processes.append(
                        (
                            int(fields[0]),
                            int(fields[1]),
                            int(fields[2]),
                            float(fields[3]),
                        )
                    )
            family = {pid}
            for _ in range(4):
                family.update(p for p, parent, *_ in processes if parent in family)
            selected = [p for p in processes if p[0] in family]
            sample.update(
                epoch_ms=time.time_ns() / 1e6,
                api_rss_sum_bytes=sum(p[2] * 1024 for p in selected),
                api_cpu_pct_sum=sum(p[3] for p in selected),
                api_processes=len(selected),
            )
            f.write(json.dumps(sample) + "\n")
            await asyncio.sleep(args.interval)


async def describe(args) -> None:
    """Read-only post-run metadata; omit connection strings and private fixture tokens."""
    values = load(args.state)
    import importlib.metadata

    import asyncpg
    from sqlalchemy.engine import make_url

    from app.core.redis import get_pubsub_redis, get_redis

    # The migration owner can read alembic_version; this session still cannot write.
    url = make_url(values["environment"]["DATABASE_URL_MIGRATIONS"])
    owner = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        revision = await owner.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await owner.close()
    # Operational tables force RLS, including for the owner. The existing reporting
    # role is required for a complete cross-tenant read, as in the accounting oracle.
    url = make_url(values["environment"]["DATABASE_URL_PLATFORM"])
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        settings = await conn.fetch(
            """SELECT name,setting,unit FROM pg_settings WHERE name = ANY($1::text[])
            ORDER BY name""",
            [
                "max_connections",
                "shared_buffers",
                "work_mem",
                "fsync",
                "synchronous_commit",
                "full_page_writes",
                "statement_timeout",
            ],
        )
        states = await conn.fetch("SELECT state::text, count(*) AS count FROM simulation_matches GROUP BY state")
        result = {
            "captured_at": datetime.now(UTC).isoformat(),
            "database": values["database"],
            "postgres_version": await conn.fetchval("SELECT version()"),
            "migration": revision,
            "postgres_settings": [dict(row) for row in settings],
            "match_states": {row["state"]: row["count"] for row in states},
            "unsettled_stakes": await conn.fetchval(
                """SELECT count(*) FROM simulation_stakes s JOIN simulation_matches m
                ON m.id=s.match_id AND m.tenant_id=s.tenant_id WHERE m.state <> 'settled'"""
            ),
            "packages": {
                name: importlib.metadata.version(name) for name in ("fastapi", "uvicorn", "redis", "asyncpg", "SQLAlchemy")
            },
            "redis_pool_max_connections_per_worker": get_redis().connection_pool.max_connections,
            "redis_pubsub_max_connections_per_worker": get_pubsub_redis().connection_pool.max_connections,
            "redis_command_pool_type": type(get_redis().connection_pool).__name__,
            "workers": values["workers"],
            "application_db_pool_size_per_worker": int(values["environment"]["DB_POOL_SIZE"]),
            "application_db_max_overflow_per_worker": int(values["environment"]["DB_MAX_OVERFLOW"]),
        }
    finally:
        await conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


def serve(args) -> None:
    values = load(args.state)
    (args.state / "api.pid").write_text(str(os.getpid()))
    # Observe pre-publish timestamps only in this isolated benchmark worker. Product payloads
    # and application code are unchanged. Every worker writes its own append-only trace.
    os.environ["BENCHMARK_TRACE_DIR"] = str(args.state)
    import uvicorn

    uvicorn.run(
        "benchmark_asgi:app",
        app_dir=str(Path(__file__).parent),
        host="127.0.0.1",
        port=values["port"],
        workers=values["workers"],
        log_level="warning",
        access_log=False,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("prepare", "serve", "monitor", "audit", "probe", "describe"))
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--database", default="")
    p.add_argument("--players", type=int, default=5000)
    p.add_argument("--burst", type=int, default=500)
    p.add_argument("--tenants", type=int, default=10)
    p.add_argument("--ws-clients", type=int, default=1000)
    p.add_argument("--port", type=int, default=8015)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", type=Path, default=Path("/tmp/benchmark-monitor.jsonl"))  # noqa: S108 -- public telemetry only
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--label", default="final")
    p.add_argument("--match", default="")
    args = p.parse_args()
    args.state = args.state.resolve()
    if args.command == "serve":
        serve(args)
    else:
        asyncio.run(
            {"prepare": prepare, "monitor": monitor, "audit": audit, "probe": probe, "describe": describe}[args.command](args)
        )


if __name__ == "__main__":
    main()
