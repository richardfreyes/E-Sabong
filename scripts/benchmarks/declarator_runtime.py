"""Local-only real Declarator soak runtime. Credentials remain in an owner-only state file."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def load(state: Path) -> dict:
    data = json.loads(state.read_text())
    if not re.fullmatch(r"esabong_test_declarator_[a-z0-9_]+", data["database"]):
        raise ValueError("Isolated Declarator database required")
    for key in (
        "DATABASE_URL_APP",
        "DATABASE_URL_PLATFORM",
        "DATABASE_URL_MIGRATIONS",
        "REDIS_URL",
    ):
        if urlsplit(data["environment"][key]).hostname not in (
            "127.0.0.1",
            "localhost",
        ):
            raise ValueError("Loopback services only")
    os.environ.update(data["environment"])
    sys.path.insert(0, str(Path(data["source"]) / "api"))
    return data


async def prepare(args) -> None:
    if args.state.exists():
        raise ValueError("Choose a fresh state path; existing fixtures are never overwritten")
    if not re.fullmatch(r"esabong_test_declarator_[a-z0-9_]+", args.database):
        raise ValueError("Isolated Declarator database required")
    env = {
        "ENVIRONMENT": "development",
        "LOG_LEVEL": "WARNING",
        "LOG_JSON": "false",
        "BASE_DOMAIN": "test.local",
        "PLATFORM_HOST": "owner.test.local",
        "PUBLIC_SCHEME": "http",
        "COOKIE_SECURE": "false",
        "ALLOW_TENANT_HEADER": "false",
        "DEPLOYMENT_MODE": "shared",
        "DB_POOL_SIZE": "10",
        "DB_MAX_OVERFLOW": "10",
        "REDIS_URL": f"redis://:dev-redis@localhost:6379/{args.redis_db}",
        "CELERY_BROKER_URL": f"redis://:dev-redis@localhost:6379/{args.redis_db}",
        "CELERY_RESULT_BACKEND": f"redis://:dev-redis@localhost:6379/{args.redis_db}",
        "JWT_SECRET": secrets.token_hex(32),
        "ACCESS_TOKEN_TTL_SECONDS": "900",
        "MEDIA_ROOT": str(args.state.parent / "declarator-media"),
        "LOGIN_MAX_ATTEMPTS": "100",
        "ROOT_TENANT_SLUG": "soak",
    }
    for role in ("app", "platform", "migrations"):
        identity = "owner" if role == "migrations" else role
        env[f"DATABASE_URL_{role.upper()}"] = (
            f"postgresql+asyncpg://esabong_{identity}:dev-{identity}@localhost:5432/{args.database}"
        )
    args.state.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(
            {
                "database": args.database,
                "source": str(args.source.resolve()),
                "environment": env,
                "port": args.port,
            },
            file,
        )
    data = load(args.state)
    run = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(data["source"]) / "api",
        env={**os.environ, "ALEMBIC_DATABASE_URL": env["DATABASE_URL_MIGRATIONS"]},
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode:
        raise RuntimeError(run.stderr)
    from app.cli import demo_tenant
    from app.core.database import dispose_engines
    from app.core.redis import close_redis

    with contextlib.redirect_stdout(io.StringIO()):
        await demo_tenant("soak", "DemoPass#2026")
    await dispose_engines()
    await close_redis()
    print(json.dumps({"prepared": args.database, "port": args.port, "migration": "head"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "serve"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--database", default="esabong_test_declarator_soak")
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--redis-db", type=int, choices=range(3, 11), default=10)
    parser.add_argument("--port", type=int, default=8104)
    args = parser.parse_args()
    if args.command == "prepare":
        asyncio.run(prepare(args))
    else:
        data = load(args.state)
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=data["port"],
            log_level="warning",
            access_log=False,
        )


if __name__ == "__main__":
    main()
