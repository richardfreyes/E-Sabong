#!/usr/bin/env python3
"""Read-only production preflight. Changes nothing; prints PASS / WARN / FAIL per check.

    python3 infrastructure/scripts/preflight.py                 # on the server, in /opt/esabong
    python3 infrastructure/scripts/preflight.py --env-file staging.env --compose-file docker-compose.yml \
        --compose-file docker-compose.prod.yml --skip-host      # review a config from a workstation

Checks
  compose   merged config: only 80/443 published, postgres/redis unpublished and on an internal
            network, restart policies, memory limits, healthchecks, named volumes for data
  secrets   lengths and distinctness of passwords/JWT secret (values are never printed),
            the API's own production_problems() via the api image
  files     .env / .offsite.env / .monitor.env mode 600, db/backups mode 700
  host      listening TCP sockets on non-loopback addresses other than 22/80/443
  oci       (if the OCI CLI is configured and OCI_SECURITY_LIST_IDS / OCI_NSG_IDS are set)
            ingress rules open to 0.0.0.0/0 on ports other than 80/443 (22 is a warning)
Exit code: 1 if any FAIL.
Requires: Python 3.8+, Docker with the compose plugin; `ss` (Linux) for the host check.
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys

results: list[tuple[str, str, str]] = []


def report(level: str, area: str, message: str) -> None:
    results.append((level, area, message))


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # noqa: S603 -- fixed argv, no shell


def read_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"\s+#.*$", "", value).strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


# ------------------------------------------------------------------ compose


def check_compose(files: list[str], env_file: str) -> None:
    cmd = ["docker", "compose", "--env-file", env_file]
    for f in files:
        cmd += ["-f", f]
    proc = run([*cmd, "config", "--format", "json"])
    if proc.returncode != 0:
        report("FAIL", "compose", "docker compose config failed: " + proc.stderr.strip().splitlines()[-1][:300])
        return
    cfg = json.loads(proc.stdout)
    services = cfg.get("services", {})
    networks = cfg.get("networks", {})
    for name, svc in services.items():
        if "dev" in (svc.get("profiles") or []):
            continue
        published = [str(p.get("published")) for p in svc.get("ports", []) if p.get("published")]
        if name in ("postgres", "redis"):
            if published:
                report("FAIL", "compose", f"{name} publishes host port(s) {', '.join(published)}")
            internal = [n for n in (svc.get("networks") or {}) if networks.get(n, {}).get("internal")]
            if not internal:
                report("FAIL", "compose", f"{name} is not on an internal-only network")
        elif name == "caddy":
            extra = sorted(set(published) - {"80", "443"})
            if extra:
                report("FAIL", "compose", f"caddy publishes unexpected ports {', '.join(extra)}")
        elif published:
            report("FAIL", "compose", f"{name} publishes host port(s) {', '.join(published)}")
        if name != "migrate" and svc.get("restart") not in ("unless-stopped", "always"):
            report("FAIL", "compose", f"{name} has restart policy {svc.get('restart')!r}")
        limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        if name != "migrate" and not (limits.get("memory") or svc.get("mem_limit")):
            report("WARN", "compose", f"{name} has no memory limit")
        if name in ("postgres", "redis", "api", "worker", "caddy") and not svc.get("healthcheck"):
            report("FAIL", "compose", f"{name} has no healthcheck")
        env = svc.get("environment") or {}
        if name in ("api", "worker", "beat", "migrate") and env.get("ENVIRONMENT") != "production":
            report("FAIL", "compose", f"{name} runs with ENVIRONMENT={env.get('ENVIRONMENT')!r}")
        if name == "caddy" and env.get("CADDY_TLS_MODE") != "acme":
            report("FAIL", "compose", f"caddy CADDY_TLS_MODE={env.get('CADDY_TLS_MODE')!r} (public certificates need acme)")
        if (svc.get("logging") or {}).get("driver") != "json-file" or not (svc.get("logging") or {}).get("options", {}).get(
            "max-size"
        ):
            report("WARN", "compose", f"{name} has no log rotation")
    for name, mount in (("postgres", "/var/lib/postgresql/data"), ("redis", "/data"), ("caddy", "/data")):
        vols = services.get(name, {}).get("volumes", [])
        if not any(v.get("target") == mount and v.get("type") == "volume" for v in vols):
            report("FAIL", "compose", f"{name} {mount} is not on a named volume (data would not persist)")
    if not any(level == "FAIL" and area == "compose" for level, area, _ in results):
        report(
            "PASS",
            "compose",
            f"{len(services)} services: only caddy 80/443 published; data services internal; "
            "restart, healthchecks and volumes in place",
        )


# ------------------------------------------------------------------ secrets


def check_secrets(env: dict[str, str], files: list[str], env_file: str, skip_image: bool, api_image: str | None = None) -> None:
    secrets = ["POSTGRES_SUPERUSER_PASSWORD", "DB_OWNER_PASSWORD", "DB_APP_PASSWORD", "DB_PLATFORM_PASSWORD", "REDIS_PASSWORD"]
    weak = [k for k in secrets if len(env.get(k, "")) < 24 or "CHANGE_ME" in env.get(k, "") or env.get(k, "").startswith("dev-")]
    if weak:
        report("FAIL", "secrets", "weak, placeholder or missing (need 24+ random characters): " + ", ".join(weak))
    values = [env.get(k, "") for k in secrets if env.get(k)]
    if len(values) != len(set(values)):
        report("FAIL", "secrets", "database/redis passwords are not all distinct")
    jwt = env.get("JWT_SECRET", "")
    if len(jwt) < 48 or "CHANGE" in jwt:
        report("FAIL", "secrets", "JWT_SECRET must be 48+ random characters (openssl rand -base64 48)")
    if env.get("JWT_SECRET") and env.get("JWT_SECRET") in values:
        report("FAIL", "secrets", "JWT_SECRET reuses a database password")
    for key in ("CORS_ORIGINS",):
        if "*" in env.get(key, ""):
            report("FAIL", "secrets", f"{key} contains a wildcard")
    if env.get("API_DOCS_ENABLED", "").lower() in ("1", "true", "yes"):
        report("WARN", "secrets", "API_DOCS_ENABLED=true: the API schema is public")
    if env.get("LOG_LEVEL", "INFO").upper() == "DEBUG":
        report("FAIL", "secrets", "LOG_LEVEL=DEBUG in production")
    if not skip_image and not api_image:
        report("WARN", "secrets", "image startup check not run; supply --api-image with an already-built review image")
    if not skip_image and api_image:
        cmd = ["docker", "compose", "--env-file", env_file]
        for f in files:
            cmd += ["-f", f]
        config = run([*cmd, "config", "--format", "json"])
        if config.returncode:
            report("FAIL", "secrets", "cannot resolve the API environment for the image check")
            return
        settings = json.loads(config.stdout)["services"]["api"]["environment"]
        settings["ENVIRONMENT"] = "development"  # collect every production problem without printing validation inputs
        code = (
            "import json, os, sys; os.environ.update({k:str(v) for k,v in json.load(sys.stdin).items() if v is not None}); "
            "from app.core.config import Settings, production_problems; "
            "p = production_problems(Settings()); print(''.join('PROBLEM: ' + x + chr(10) for x in p)); sys.exit(1 if p else 0)"
        )
        # No compose up/down/build, network, application startup, credentials in argv,
        # or attached volumes. The chosen image only evaluates configuration.
        proc = run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--entrypoint",
                "python",
                api_image,
                "-c",
                code,
            ],
            input=json.dumps(settings),
        )
        if proc.returncode == 0:
            report("PASS", "secrets", "API startup checks (production_problems) report nothing")
        else:
            lines = [ln[9:] for ln in proc.stdout.splitlines() if ln.startswith("PROBLEM: ")]
            lines = lines or [(proc.stderr.strip().splitlines() or ["the api image did not run"])[-1][:200]]
            report("FAIL", "secrets", "API startup checks: " + "; ".join(lines))
    if not any(level == "FAIL" and area == "secrets" for level, area, _ in results):
        report("PASS", "secrets", "passwords and JWT secret long, distinct and not placeholders (values not shown)")


# ------------------------------------------------------------------ files


def check_files() -> None:
    for path, wanted in ((".env", 0o600), (".offsite.env", 0o600), (".monitor.env", 0o600), ("db/backups", 0o700)):
        if not os.path.exists(path):
            if path == ".env":
                report("FAIL", "files", ".env is missing")
            elif path != "db/backups":
                report("WARN", "files", f"{path} not present (feature not configured yet)")
            continue
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            report("FAIL", "files", f"{path} is mode {oct(mode)[2:]} (should be {oct(wanted)[2:]}: readable by other users)")
        else:
            report("PASS", "files", f"{path} mode {oct(mode)[2:]}")
    for dump in (
        sorted(f for f in os.listdir("db/backups") if f.endswith((".dump", ".tar.gz"))) if os.path.isdir("db/backups") else []
    ):
        if stat.S_IMODE(os.stat(os.path.join("db/backups", dump)).st_mode) & 0o077:
            report("FAIL", "files", f"backup db/backups/{dump} is readable by other users")


# ------------------------------------------------------------------ host


def check_host() -> None:
    if not shutil.which("ss"):
        report("WARN", "host", "`ss` not available: listening-socket check skipped (run on the Linux server)")
        return
    proc = run(["ss", "-H", "-ltn"])
    exposed = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        addr, _, port = parts[3].rpartition(":")
        if addr.strip("[]") in ("127.0.0.1", "::1") or addr.startswith("127."):
            continue
        if port not in ("22", "80", "443"):
            exposed.append(parts[3])
    if exposed:
        report("FAIL", "host", "listening on public interfaces besides 22/80/443: " + ", ".join(sorted(set(exposed))))
    else:
        report("PASS", "host", "only 22/80/443 listen on non-loopback addresses")


# ------------------------------------------------------------------ oci


def check_oci() -> None:
    lists = [x for x in os.environ.get("OCI_SECURITY_LIST_IDS", "").split(",") if x]
    nsgs = [x for x in os.environ.get("OCI_NSG_IDS", "").split(",") if x]
    if not shutil.which("oci") or not (lists or nsgs):
        report(
            "WARN",
            "oci",
            "OCI CLI or OCI_SECURITY_LIST_IDS/OCI_NSG_IDS not set: verify in the console that ingress "
            "from 0.0.0.0/0 allows only TCP 80 and 443 (SSH only from admin IPs or the OCI Bastion)",
        )
        return
    rules: list[tuple[str, dict]] = []
    for sl in lists:
        proc = run(["oci", "network", "security-list", "get", "--security-list-id", sl])
        if proc.returncode != 0:
            report("FAIL", "oci", f"cannot read security list {sl[-12:]}: {proc.stderr.strip()[:160]}")
            continue
        rules += [(f"security list …{sl[-8:]}", r) for r in json.loads(proc.stdout)["data"].get("ingress-security-rules", [])]
    for nsg in nsgs:
        proc = run(["oci", "network", "nsg", "rules", "list", "--nsg-id", nsg, "--direction", "INGRESS", "--all"])
        if proc.returncode != 0:
            report("FAIL", "oci", f"cannot read NSG {nsg[-12:]}: {proc.stderr.strip()[:160]}")
            continue
        rules += [(f"NSG …{nsg[-8:]}", r) for r in json.loads(proc.stdout or '{"data": []}')["data"]]
    for where, rule in rules:
        source = rule.get("source", "")
        if source not in ("0.0.0.0/0", "::/0"):
            continue
        tcp = rule.get("tcp-options") or {}
        rng = tcp.get("destination-port-range") or {}
        lo, hi = rng.get("min"), rng.get("max")
        if rule.get("protocol") == "all" or (rule.get("protocol") == "6" and lo is None):
            report("FAIL", "oci", f"{where}: all ports open to {source}")
        elif rule.get("protocol") == "6" and (lo, hi) == (22, 22):
            report("WARN", "oci", f"{where}: SSH open to {source}; restrict to admin IPs or use OCI Bastion")
        elif rule.get("protocol") == "6" and not (lo == hi and lo in (80, 443)):
            report("FAIL", "oci", f"{where}: TCP {lo}-{hi} open to {source}")
        elif rule.get("protocol") == "17":
            report("FAIL", "oci", f"{where}: UDP open to {source}")
    if not any(area == "oci" and level == "FAIL" for level, area, _ in results):
        report("PASS", "oci", f"{len(rules)} ingress rules checked: public access limited to 80/443")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--compose-file", action="append")
    parser.add_argument("--skip-host", action="store_true")
    parser.add_argument("--skip-image", action="store_true", help="do not run the api image's startup checks")
    parser.add_argument("--api-image", help="already-built image to check in a network-disabled, read-only container")
    args = parser.parse_args()
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    files = args.compose_file or ["docker-compose.yml", "docker-compose.prod.yml"]
    env = read_env(args.env_file)
    check_compose(files, args.env_file)
    check_secrets(env, files, args.env_file, args.skip_image, args.api_image)
    if args.env_file == ".env":
        check_files()
    if not args.skip_host:
        check_host()
        check_oci()
    width = max(len(a) for _, a, _ in results)
    for level, area, message in results:
        print(f"{level:4}  {area:<{width}}  {message}")
    failed = sum(1 for level, _, _ in results if level == "FAIL")
    print(f"\n{failed} FAIL, {sum(1 for r in results if r[0] == 'WARN')} WARN, {sum(1 for r in results if r[0] == 'PASS')} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
