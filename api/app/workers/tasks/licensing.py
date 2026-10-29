"""Dedicated installs: periodic license heartbeat to the SaaS control plane."""

import hashlib
import json
import socket
import urllib.error
import urllib.request

import structlog

from app.core.config import get_settings
from app.core.redis import get_redis
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async

log = structlog.get_logger("worker.licensing")
APP_VERSION = "0.1.0"


def instance_fingerprint() -> str:
    s = get_settings()
    if s.instance_fingerprint:
        return s.instance_fingerprint
    return hashlib.sha256(f"{socket.getfqdn()}:{s.dedicated_tenant_slug}".encode()).hexdigest()[:32]


def _post(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(  # noqa: S310 - URL comes from operator configuration
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@celery_app.task(name="licensing.heartbeat")
def heartbeat() -> str:
    s = get_settings()
    if s.deployment_mode != "dedicated" or not s.license_heartbeat_url or not s.license_key:
        return "skipped"
    payload = {
        "license_key": s.license_key.get_secret_value(),
        "instance_fingerprint": instance_fingerprint(),
        "hostname": socket.getfqdn(),
        "app_version": APP_VERSION,
    }
    try:
        status, body = _post(s.license_heartbeat_url, payload)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Offline: keep running on the signed token until it expires.
        log.warning("license_heartbeat_unreachable", error=str(exc))
        return "unreachable"

    async def _store() -> str:
        redis = get_redis()
        if status == 200 and body.get("license_token"):
            await redis.set("license:token", body["license_token"])
            await redis.delete("license:revoked")
            return "ok"
        error_code = (body.get("error") or {}).get("code", "")
        if (status == 200 and body.get("status") in ("revoked", "expired")) or (
            status == 403 and error_code.startswith("license_")
        ):
            await redis.set("license:revoked", "1")
            return "revoked"
        return f"http_{status}"

    result = run_async(_store)
    log.info("license_heartbeat", result=result)
    return result
