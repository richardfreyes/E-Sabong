"""Dedicated deployment licensing: issue, activate, verify offline, revoke."""

import base64
import json
from datetime import timedelta

import pytest

from app.core.security import utcnow
from app.modules.licenses import signing
from tests.conftest import client_for


async def _issue(owner, tenant, **extra) -> dict:  # type: ignore[no-untyped-def]
    body = {
        "tenant_id": str(tenant.id),
        "license_type": "term",
        "licensee_name": "Acme Arena Corp",
        "expires_at": (utcnow() + timedelta(days=365)).isoformat(),
        "max_activations": 1,
        **extra,
    }
    r = await owner.client.post("/api/v1/platform/licenses", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_issue_activate_heartbeat_revoke(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    issued = await _issue(owner, tenant)
    raw_key = issued["license_key"]
    assert raw_key.startswith("ESB-") and issued["license"]["status"] == "issued"

    dep = await owner.client.post(
        "/api/v1/platform/deployments",
        json={
            "tenant_id": str(tenant.id),
            "name": "Acme prod",
            "hostname": "events.acme.example",
            "license_id": issued["license"]["id"],
        },
    )
    assert dep.status_code == 201 and dep.json()["status"] == "provisioning"

    async with client_for(app, "owner.test.local") as c:
        body = {
            "license_key": raw_key,
            "instance_fingerprint": "fp-0000000001",
            "hostname": "events.acme.example",
            "app_version": "0.1.0",
        }
        r = await c.post("/api/v1/licensing/activate", json=body)
        assert r.status_code == 200, r.text
        token = r.json()["license_token"]
        verified = signing.verify(token)
        assert verified.tenant_slug == tenant.slug and verified.licensee == "Acme Arena Corp" and not verified.expired

        # One activation allowed: a second instance is refused, the same one may re-activate.
        r2 = await c.post("/api/v1/licensing/activate", json={**body, "instance_fingerprint": "fp-0000000002"})
        assert r2.status_code == 403 and r2.json()["error"]["code"] == "license_activation_limit"
        assert (await c.post("/api/v1/licensing/activate", json=body)).status_code == 200

        hb = await c.post("/api/v1/licensing/heartbeat", json=body)
        assert hb.status_code == 200 and hb.json()["status"] == "active" and hb.json()["license_token"]

        deployments = (await owner.client.get("/api/v1/platform/deployments", params={"tenant_id": str(tenant.id)})).json()[
            "items"
        ]
        assert deployments[0]["status"] == "active" and deployments[0]["last_heartbeat_at"]

        lic_id = issued["license"]["id"]
        assert (await owner.client.post(f"/api/v1/platform/licenses/{lic_id}/revoke", json={"reason": "Contract ended"})).json()[
            "status"
        ] == "revoked"
        hb = await c.post("/api/v1/licensing/heartbeat", json=body)
        assert hb.json()["status"] == "revoked" and hb.json()["license_token"] is None
        r = await c.post("/api/v1/licensing/activate", json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "license_revoked"


async def test_unknown_key_rejected(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, "owner.test.local") as c:
        r = await c.post(
            "/api/v1/licensing/activate",
            json={"license_key": "ESB-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", "instance_fingerprint": "fp-unknown-1"},
        )
    assert r.status_code == 403 and r.json()["error"]["code"] == "license_invalid"


async def test_perpetual_license_rules(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await owner.client.post(
        "/api/v1/platform/licenses",
        json={
            "tenant_id": str(tenant.id),
            "license_type": "perpetual",
            "licensee_name": "Forever Inc",
            "expires_at": (utcnow() + timedelta(days=1)).isoformat(),
        },
    )
    assert r.status_code == 422
    issued = await _issue(
        owner, tenant, license_type="perpetual", expires_at=None, maintenance_until=(utcnow() + timedelta(days=365)).isoformat()
    )
    assert issued["license"]["expires_at"] is None and issued["license"]["maintenance_until"]


def test_tampered_token_fails_verification() -> None:
    token = signing.sign(
        {
            "lid": "x",
            "tenant": "acme",
            "licensee": "Acme",
            "type": "term",
            "expires_at": (utcnow() + timedelta(days=1)).isoformat(),
        }
    )
    prefix, body, sig = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["expires_at"] = (utcnow() + timedelta(days=9999)).isoformat()
    forged_body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(signing.LicenseError):
        signing.verify(f"{prefix}.{forged_body}.{sig}")
    other_private, other_public = signing.generate_keypair()
    with pytest.raises(signing.LicenseError):
        signing.verify(token, public_key_pem=other_public)


def test_expired_token_detected() -> None:
    token = signing.sign(
        {
            "lid": "x",
            "tenant": "acme",
            "licensee": "Acme",
            "type": "term",
            "expires_at": (utcnow() - timedelta(seconds=1)).isoformat(),
        }
    )
    assert signing.verify(token).expired
