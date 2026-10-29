"""HTTP validation and permission dependencies, without substituting for RLS integration tests."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.deps import CurrentUser, get_current_user, get_tenant_db, require_tenant
from app.core.errors import install_error_handlers
from app.core.permissions import P
from app.modules.declarator import service
from app.modules.declarator.models import DeclarationOutcome, DeclarationStatus
from app.modules.declarator.router import router
from app.modules.users.models import AccountType


@pytest.fixture
async def contract(monkeypatch):
    tid, uid, mid, did = [uuid.uuid4() for _ in range(4)]
    actor = CurrentUser(
        user=SimpleNamespace(id=uid, username="judge", account_type=AccountType.DECLARATOR),
        tenant=SimpleNamespace(id=tid),
        permissions=frozenset({P.RESULTS_SUBMIT}),
        session_family=uuid.uuid4(),
    )
    row = SimpleNamespace(
        id=did,
        match_id=mid,
        outcome=DeclarationOutcome.MERON,
        status=DeclarationStatus.PENDING_APPROVAL,
        base_revision=0,
        reason=None,
        declared_by_id=uid,
        approved_by_id=None,
        finalized_at=None,
        created_at=datetime.now(UTC),
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[require_tenant] = lambda: actor.tenant
    app.dependency_overrides[get_tenant_db] = lambda: AsyncMock()
    from app.modules.tenants import service as tenants

    monkeypatch.setattr(tenants, "effective_modules", AsyncMock(return_value=["events", "matches"]))
    submit = AsyncMock(return_value=row)
    monkeypatch.setattr(service, "submit", submit)
    monkeypatch.setattr(service, "usernames", AsyncMock(return_value={uid: "judge"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test.local") as client:
        yield client, actor, mid, did, submit


@pytest.mark.parametrize(
    "extra",
    [
        {"request_id": None},
        {"request_id": "invalid"},
        {"outcome": "unknown"},
        {"base_revision": -1},
        {"outcome": "cancelled"},
        {"base_revision": 1},
        {"reason": "x" * 501},
        {"outcome": "cancelled", "reason": "x" * 301},
    ],
)
async def test_invalid_requests_never_reach_submission_service(contract, extra):
    client, _, mid, _, submit = contract
    r = await client.post(
        f"/api/v1/declarator/matches/{mid}/declarations", json={"request_id": str(uuid.uuid4()), "outcome": "meron", **extra}
    )
    assert r.status_code == 422
    submit.assert_not_awaited()


async def test_valid_submission_has_pending_response_and_original_actor(contract):
    client, actor, mid, _, submit = contract
    r = await client.post(
        f"/api/v1/declarator/matches/{mid}/declarations", json={"request_id": str(uuid.uuid4()), "outcome": "meron"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_approval" and r.json()["declared_by_id"] == str(actor.id)
    assert r.json()["approved_by"] is None
    submit.assert_awaited_once()


async def test_submitter_cannot_call_approval_route(contract):
    client, _, _, did, _ = contract
    r = await client.post(f"/api/v1/declarator/declarations/{did}/approve")
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"


async def test_unprivileged_user_cannot_open_desk_or_submit(contract):
    client, actor, mid, _, submit = contract
    actor.permissions = frozenset()
    assert (await client.get("/api/v1/declarator/events")).status_code == 403
    r = await client.post(
        f"/api/v1/declarator/matches/{mid}/declarations", json={"request_id": str(uuid.uuid4()), "outcome": "meron"}
    )
    assert r.status_code == 403
    submit.assert_not_awaited()
