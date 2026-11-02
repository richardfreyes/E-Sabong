"""Post-deploy smoke test. Read-only apart from sign-ins (which create sessions and audit rows).

    .venv/bin/python scripts/smoke.py --platform https://owner.example.ph \
        --tenant https://arena.example.ph --tenant-user smoke-player --tenant-password '…' \
        --owner-email ops@example.ph --owner-password '…'

Staging without public DNS: map names to an address in-process (TLS SNI stays correct):
    --resolve owner.staging.esabong.test=127.0.0.1 --resolve arena.staging.esabong.test=127.0.0.1 --insecure

Exit code 0 when every check passes. Passwords are read from the command line or the
SMOKE_OWNER_PASSWORD / SMOKE_TENANT_PASSWORD environment variables and are never printed.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
import time

import httpx

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((ok, label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return ok


def install_resolver(mapping: dict[str, str]) -> None:
    original = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = host.decode() if isinstance(host, bytes) else host
        return original(mapping.get(name, host), *args, **kwargs)

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]


async def edge(client: httpx.AsyncClient, base: str, docs_expected: bool) -> None:
    r = await client.get(f"{base}/api/health")
    check(r.status_code == 200 and r.json().get("status") == "ok", f"{base}/api/health → {r.status_code}")
    r = await client.get(f"{base}/api/ready")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check(r.status_code == 200 and body.get("database") == "ok" and body.get("redis") == "ok", f"readiness {body}")
    r = await client.get(f"{base}/api/v1/internal/tls-ask", params={"domain": "x"})
    check(r.status_code == 404, f"internal endpoints blocked at the edge → {r.status_code}")
    r = await client.get(f"{base}/api/docs")
    check((r.status_code == 200) == docs_expected, f"API docs {'enabled' if docs_expected else 'disabled'} → {r.status_code}")
    r = await client.get(f"{base}/")
    check(r.status_code == 200 and "<app-root" in r.text, f"portal HTML at {base}/ → {r.status_code}")
    hsts, csp = r.headers.get("strict-transport-security", ""), r.headers.get("content-security-policy", "")
    check("max-age=" in hsts and "frame-ancestors 'none'" in csp, "HSTS and CSP headers present")
    plain = base.replace("https://", "http://", 1)
    if ":8443" in plain:
        plain = plain.replace(":8443", ":8080")
    try:
        r = await client.get(f"{plain}/api/health", follow_redirects=False)
        check(
            r.status_code in (301, 308) and r.headers.get("location", "").startswith("https://"),
            f"HTTP redirects to HTTPS → {r.status_code}",
        )
    except httpx.HTTPError as exc:
        check(False, f"HTTP→HTTPS redirect check failed: {type(exc).__name__}")


async def owner(client: httpx.AsyncClient, base: str, email: str, password: str) -> None:
    r = await client.post(f"{base}/api/v1/platform/auth/login", json={"email": email, "password": password})
    if not check(r.status_code == 200, f"owner sign-in → {r.status_code}"):
        return
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    plans = (await client.get(f"{base}/api/v1/platform/plans", headers=headers)).json()
    review = [p["code"] for p in plans if p.get("is_active") and not p.get("checkout_ready")]
    for_sale = len([p for p in plans if p.get("checkout_ready")])
    check(True, f"{len(plans)} plans; {for_sale} for sale; active needing review: {review or 'none'}")
    overview = await client.get(f"{base}/api/v1/platform/analytics/overview", headers=headers)
    check(overview.status_code == 200, f"owner analytics → {overview.status_code}")


async def tenant(client: httpx.AsyncClient, base: str, username: str, password: str, ws_base: str) -> None:
    from httpx_ws import aconnect_ws

    r = await client.get(f"{base}/api/v1/public/tenant")
    check(r.status_code == 200 and "branding" in r.json(), f"tenant config at {base} → {r.status_code}")
    for path in ("/", "/admin/"):
        r = await client.get(f"{base}{path}")
        check(r.status_code == 200 and "<app-root" in r.text, f"portal HTML at {base}{path} → {r.status_code}")
    r = await client.post(f"{base}/api/v1/auth/login", json={"username": username, "password": password})
    if not check(r.status_code == 200, f"tenant sign-in → {r.status_code}"):
        return
    token = r.json()["access_token"]
    me = await client.get(f"{base}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    check(me.status_code == 200, f"profile → {me.status_code}")
    started = time.perf_counter()
    try:
        async with aconnect_ws(f"{ws_base}/api/ws?token={token}", client) as ws:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
            await ws.send_text(json.dumps({"type": "ping"}))
            pong = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        ok = hello.get("type") == "connected" and pong.get("type") == "pong"
        check(ok, f"WebSocket connected and answered ping in {round((time.perf_counter() - started) * 1000)} ms")
    except Exception as exc:  # noqa: BLE001
        check(False, f"WebSocket failed: {type(exc).__name__}")
    async with aconnect_ws(f"{ws_base}/api/ws?token=invalid", client) as ws:
        try:
            await asyncio.wait_for(ws.receive_text(), 10)
            check(False, "WebSocket accepted an invalid token")
        except Exception as exc:  # noqa: BLE001
            check(getattr(exc, "code", None) == 4401, f"WebSocket rejects an invalid token (close {getattr(exc, 'code', '?')})")


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--platform", required=True, help="https://owner.<base-domain>")
    p.add_argument("--tenant", help="https://<tenant>.<base-domain>")
    p.add_argument("--tenant-user")
    p.add_argument("--tenant-password", default=os.environ.get("SMOKE_TENANT_PASSWORD"))
    p.add_argument("--owner-email")
    p.add_argument("--owner-password", default=os.environ.get("SMOKE_OWNER_PASSWORD"))
    p.add_argument("--docs-enabled", action="store_true", help="expect API_DOCS_ENABLED=true")
    p.add_argument("--resolve", action="append", default=[], help="host=address (repeatable)")
    p.add_argument("--insecure", action="store_true", help="accept an internal/staging CA")
    a = p.parse_args()
    install_resolver(dict(item.split("=", 1) for item in a.resolve))
    async with httpx.AsyncClient(verify=not a.insecure, timeout=20, headers={"X-Requested-With": "XMLHttpRequest"}) as client:
        await edge(client, a.platform, a.docs_enabled)
        if a.owner_email and a.owner_password:
            await owner(client, a.platform, a.owner_email, a.owner_password)
        if a.tenant:
            await edge(client, a.tenant, a.docs_enabled)
            if a.tenant_user and a.tenant_password:
                await tenant(client, a.tenant, a.tenant_user, a.tenant_password, a.tenant)
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
