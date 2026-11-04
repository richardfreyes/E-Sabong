#!/usr/bin/env python3
"""Read-only isolation checks for the Contabo layout (UAT + production on one VPS).

    # Two env files: distinct secrets, hosts and ports; UAT capped below production;
    # production never enables synthetic data; memory limits fit the VPS.
    python3 infrastructure/scripts/check-env-isolation.py envs /opt/esabong/prod/.env /opt/esabong/uat/.env \
        [--gateway-env /opt/esabong/prod/infrastructure/gateway/.env] [--templates] [--vps-memory 12g]

    # One environment's merged compose config: everything it creates or mounts belongs to it.
    docker compose config --format json | python3 infrastructure/scripts/check-env-isolation.py compose prod

Values are never printed. Prints PASS / WARN / FAIL lines; exit code 1 if any FAIL.
--templates checks the committed *.example files (placeholders allowed, equal placeholders ignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

STACKS = ("prod", "uat")
CONTABO_FILES = "docker-compose.yml:docker-compose.prod.yml:docker-compose.contabo.yml"
SECRET_KEYS = (
    "POSTGRES_SUPERUSER_PASSWORD",
    "DB_OWNER_PASSWORD",
    "DB_APP_PASSWORD",
    "DB_PLATFORM_PASSWORD",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "VIDEO_CREDENTIALS_KEY",
    "MEDIAMTX_API_PASSWORD",
    "STREAM_TURN_SECRET",
    "LICENSE_PRIVATE_KEY_PEM",
    "SMTP_PASSWORD",
)
# Host ports each environment publishes (defaults as in docker-compose.yml).
PORT_KEYS = {"STREAM_RTMP_PORT": "1935", "STREAM_RTMPS_PORT": "1936", "STREAM_WEBRTC_PORT": "8189"}
# Memory limits per environment (defaults from docker-compose.prod.yml); beat is fixed at 256m.
MEMORY_KEYS = {
    "API_MEMORY": "1536m",
    "WORKER_MEMORY": "768m",
    "PG_MEMORY": "2g",
    "REDIS_MEMORY": "512m",
    "CADDY_MEMORY": "512m",
    "MEDIAMTX_MEMORY": "2g",
}
BEAT_MEMORY = 256 * 2**20
OS_RESERVE = int(1.5 * 2**30)  # kernel, Docker daemon, sshd, page cache headroom

results: list[tuple[str, str]] = []


def report(level: str, message: str) -> None:
    results.append((level, message))


def read_env(path: str) -> dict[str, str]:
    """KEY=VALUE lines as docker compose reads them (comments, one pair of quotes)."""
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            else:
                value = re.sub(r"\s+#.*$", "", value)
            env[key] = value
    return env


def size_bytes(value: str) -> int:
    m = re.fullmatch(r"(?i)\s*(\d+(?:\.\d+)?)\s*([kmg]?)i?b?\s*", value)
    if not m:
        raise ValueError(value)
    return int(float(m.group(1)) * {"": 1, "k": 2**10, "m": 2**20, "g": 2**30}[m.group(2).lower()])


def placeholder(value: str) -> bool:
    return not value or "CHANGE_ME" in value


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def env_memory(env: dict[str, str]) -> int:
    total = BEAT_MEMORY
    for key, default in MEMORY_KEYS.items():
        if key == "MEDIAMTX_MEMORY" and "streaming" not in env.get("COMPOSE_PROFILES", ""):
            continue
        total += size_bytes(env.get(key) or default)
    return total


def check_envs(prod_path: str, uat_path: str, gateway_path: str | None, templates: bool, vps_memory: int) -> None:
    prod, uat = read_env(prod_path), read_env(uat_path)
    envs = {"prod": prod, "uat": uat}

    for stack, env in envs.items():
        if env.get("ESB_STACK") != stack:
            report("FAIL", f"{stack} env file has ESB_STACK={env.get('ESB_STACK')!r} (expected {stack!r})")
        if env.get("COMPOSE_FILE") != CONTABO_FILES:
            report("FAIL", f"{stack}: COMPOSE_FILE must be {CONTABO_FILES}")
        if env.get("COMPOSE_PROJECT_NAME"):
            report("FAIL", f"{stack}: remove COMPOSE_PROJECT_NAME (the project name comes from ESB_STACK)")
        if env.get("ENVIRONMENT") != "production":
            report("FAIL", f"{stack}: ENVIRONMENT must be production (UAT mirrors production safety checks)")

    # Production never runs synthetic data or test patterns.
    for key in ("SYNTHETIC_DATA_ENABLED", "STREAM_TEST_SOURCE", "ALLOW_TENANT_HEADER", "API_DOCS_ENABLED"):
        if truthy(prod.get(key)):
            report("FAIL", f"prod: {key} must be off in production")

    # Secrets: every value distinct across the two environments (and the gateway's UAT token).
    prod_values = {k: v for k, v in prod.items() if k in SECRET_KEYS and v}
    uat_values = {k: v for k, v in uat.items() if k in SECRET_KEYS and v}
    shared = sorted(
        f"{pk}/{uk}"
        for pk, pv in prod_values.items()
        for uk, uv in uat_values.items()
        if pv == uv and not (templates and placeholder(pv))
    )
    if shared:
        report("FAIL", "secrets shared between prod and uat: " + ", ".join(shared))
    else:
        report("PASS", f"{len(prod_values)} prod and {len(uat_values)} uat secrets, none shared (values not shown)")
    if not templates:
        weak = [f"{s}:{k}" for s, env in envs.items() for k, v in env.items() if k in SECRET_KEYS and v and placeholder(v)]
        if weak:
            report("FAIL", "placeholder secrets left: " + ", ".join(sorted(weak)))
    if gateway_path:
        gw = read_env(gateway_path)
        token = gw.get("UAT_ACCESS_TOKEN", "")
        if not templates and not re.fullmatch(r"[A-Za-z0-9]{32,}", token):
            report("FAIL", "gateway: UAT_ACCESS_TOKEN must be 32+ letters/digits")
        if token and not placeholder(token) and token in {*prod_values.values(), *uat_values.values()}:
            report("FAIL", "gateway: UAT_ACCESS_TOKEN reuses an environment secret")
        for key, stack in (("PROD_HOST", "prod"), ("UAT_HOST", "uat")):
            if gw.get(key) and gw.get(key) != envs[stack].get("BASE_DOMAIN"):
                report("FAIL", f"gateway {key}={gw.get(key)} but {stack} BASE_DOMAIN={envs[stack].get('BASE_DOMAIN')}")

    # Hosts.
    for key in ("BASE_DOMAIN", "PLATFORM_HOST"):
        if prod.get(key) and prod.get(key) == uat.get(key):
            report("FAIL", f"{key} is the same in prod and uat")
    for stack, env in envs.items():
        if not env.get("ROOT_TENANT_SLUG"):
            report("WARN", f"{stack}: ROOT_TENANT_SLUG is empty: https://{env.get('BASE_DOMAIN')}/ will answer 'no tenant'")
        if env.get("PLATFORM_HOST") != env.get("BASE_DOMAIN"):
            report("WARN", f"{stack}: PLATFORM_HOST differs from BASE_DOMAIN; /owner/ is served only on PLATFORM_HOST")

    # Ports: each environment's published media ports are distinct from the other's and from 80/443.
    used: dict[str, str] = {}
    clash = []
    for stack, env in envs.items():
        for key, default in PORT_KEYS.items():
            port = env.get(key) or default
            if port in ("80", "443"):
                clash.append(f"{stack}:{key}={port}")
            if port in used:
                clash.append(f"{used[port]} and {stack}:{key} both use {port}")
            used[port] = f"{stack}:{key}"
    if clash:
        report("FAIL", "port conflicts: " + "; ".join(clash))
    else:
        report("PASS", "media ports distinct: " + ", ".join(f"{v}={k}" for k, v in sorted(used.items(), key=lambda i: i[1])))

    # UAT is weighted and capped below production.
    if int(uat.get("CPU_SHARES") or 1024) >= int(prod.get("CPU_SHARES") or 1024):
        report("FAIL", "uat CPU_SHARES must be lower than prod (e.g. 256 vs 1024)")
    if int(uat.get("OOM_SCORE_ADJ") or 0) <= int(prod.get("OOM_SCORE_ADJ") or 0):
        report("FAIL", "uat OOM_SCORE_ADJ must be higher than prod (e.g. 500 vs 0): the kernel then kills UAT first")
    for key, default in MEMORY_KEYS.items():
        if size_bytes(uat.get(key) or default) > size_bytes(prod.get(key) or default):
            report("WARN", f"uat {key} is larger than prod's")

    try:
        gw_mem = size_bytes(read_env(gateway_path).get("GATEWAY_MEMORY") or "256m") if gateway_path else 256 * 2**20
        totals = {s: env_memory(env) for s, env in envs.items()}
    except ValueError as exc:
        report("FAIL", f"unreadable memory limit {exc}")
        return
    total = sum(totals.values()) + gw_mem
    budget = vps_memory - OS_RESERVE
    line = (
        f"memory limits: prod {totals['prod'] / 2**30:.2f} GiB + uat {totals['uat'] / 2**30:.2f} GiB"
        f" + gateway {gw_mem / 2**30:.2f} GiB = {total / 2**30:.2f} GiB of {budget / 2**30:.2f} GiB usable"
    )
    report("FAIL" if total > budget else "PASS", line)


def check_compose(stack: str, cfg: dict, checkout: str) -> None:  # type: ignore[type-arg]
    prefix = f"esabong-{stack}"
    if cfg.get("name") != prefix:
        report("FAIL", f"compose project is {cfg.get('name')!r}, expected {prefix!r}")
    for key, vol in (cfg.get("volumes") or {}).items():
        name = (vol or {}).get("name") or ""
        if (vol or {}).get("external") or not name.startswith(prefix + "_"):
            report("FAIL", f"volume {key} -> {name!r} is not owned by {prefix}")
    for key, net in (cfg.get("networks") or {}).items():
        name = (net or {}).get("name") or ""
        if key == "gateway":
            if name != f"{prefix}-gateway" or not (net or {}).get("external"):
                report("FAIL", f"gateway network is {name!r}, expected external {prefix}-gateway")
        elif (net or {}).get("external") or not name.startswith(prefix + "_"):
            report("FAIL", f"network {key} -> {name!r} is not owned by {prefix}")
    other = [s for s in STACKS if s != stack]
    root = os.path.realpath(checkout)
    for svc_name, svc in (cfg.get("services") or {}).items():
        if svc.get("container_name"):
            report("FAIL", f"{svc_name} sets container_name (would collide across environments)")
        for vol in svc.get("volumes") or []:
            if vol.get("type") == "bind":
                src = os.path.realpath(vol.get("source") or "")
                if not (src == root or src.startswith(root + os.sep)):
                    report("FAIL", f"{svc_name} bind-mounts {src} from outside this checkout")
        env = svc.get("environment") or {}
        for key, value in env.items():
            text = str(value or "")
            if any(f"esabong-{o}" in text for o in other):
                report("FAIL", f"{svc_name} {key} refers to the other environment")
            if key.startswith(("DATABASE_URL", "REDIS_URL", "CELERY_")) and text:
                host = re.sub(r"^[a-z+]+://(?:[^@/]*@)?([^:/]+).*$", r"\1", text)
                if host not in ("postgres", "redis"):
                    report("FAIL", f"{svc_name} {key} points at host {host!r} instead of this stack's own service")
        for net in svc.get("networks") or {}:
            if any(net.startswith(f"esabong-{o}") for o in other):
                report("FAIL", f"{svc_name} joins network {net} of another environment")
    if not any(level == "FAIL" for level, _ in results):
        report(
            "PASS",
            f"{prefix}: project, {len(cfg.get('volumes') or {})} volumes, {len(cfg.get('networks') or {})} networks,"
            " bind mounts and data-service URLs all belong to this environment",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    p_envs = sub.add_parser("envs")
    p_envs.add_argument("prod_env")
    p_envs.add_argument("uat_env")
    p_envs.add_argument("--gateway-env")
    p_envs.add_argument("--templates", action="store_true")
    p_envs.add_argument("--vps-memory", default="12g")
    p_compose = sub.add_parser("compose")
    p_compose.add_argument("stack", choices=STACKS)
    p_compose.add_argument("--checkout", default=".")
    args = parser.parse_args()
    if args.mode == "envs":
        check_envs(args.prod_env, args.uat_env, args.gateway_env, args.templates, size_bytes(args.vps_memory))
    else:
        check_compose(args.stack, json.load(sys.stdin), args.checkout)
    for level, message in results:
        print(f"{level:4}  {message}")
    return 1 if any(level == "FAIL" for level, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
