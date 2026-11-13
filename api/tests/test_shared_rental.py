"""Shared-host identities, rental billing and independent global-fight settlement."""

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import delete

from app.core.database import platform_session
from app.modules.tenants.global_arena import projection_id
from app.modules.tenants.rental import RentalSettings
from app.modules.video.models import VideoStream
from tests.conftest import PASSWORD, client_for, unique
from tests.test_betting import enable, open_card, placed, pools, view

ROOT = "test.local"
RENTAL = "/api/v1/platform/rental"


@pytest.fixture(autouse=True)
async def clear_rental_settings():
    yield
    async with platform_session() as db:
        await db.execute(delete(RentalSettings))


async def configure(owner, tenant, **extra):
    body = {"shared_domain_enabled": True, "broadcast_tenant_id": str(tenant.id), "default_tenant_id": str(tenant.id), **extra}
    r = await owner.client.put(f"{RENTAL}/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@asynccontextmanager
async def login_on_shared(app, tenant, username):
    c = client_for(app, ROOT)
    r = await c.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["tenant"]["id"] == str(tenant.id)
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    try:
        yield c
    finally:
        await c.aclose()


async def test_shared_login_refresh_registration_and_isolation(app, owner, tenant, other_tenant, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "register_ip_max_per_hour", 10000)
    a = await tenant.create_user(unique("agent"), "master_agent")
    b = await other_tenant.create_user(unique("agent"), "master_agent")
    await configure(owner, tenant)
    async with client_for(app, ROOT) as public:
        created = await public.post(
            "/api/v1/auth/register",
            json={
                "username": unique("player"),
                "full_name": "Player",
                "password": PASSWORD,
                "ref": b["id"],
                "tenant_id": str(tenant.id),  # Untrusted ownership fields cannot override the invitation.
                "parent_id": a["id"],
            },
        )
        assert created.status_code == 201, created.text
        data = created.json()
        assert data["user"]["tenant"]["id"] == str(other_tenant.id)
        assert data["user"]["parent_id"] == b["id"]
        assert data["user"]["tenant"]["player_url"] == "http://test.local"
        public.headers["Authorization"] = f"Bearer {data['access_token']}"
        assert (await public.get("/api/v1/auth/me")).json()["tenant"]["id"] == str(other_tenant.id)
        public.headers["Authorization"] = "Bearer expired-token-must-not-select-refresh-tenant"
        refresh = await public.post("/api/v1/auth/refresh")
        assert refresh.status_code == 200, refresh.text
        public.headers["Authorization"] = f"Bearer {refresh.json()['access_token']}"
        assert (await public.get("/api/v1/auth/me")).json()["id"] == data["user"]["id"]
    async with login_on_shared(app, tenant, a["username"]) as actor:
        # Signed tenant context, not a request header, decides ownership.
        actor.headers["X-Tenant-Slug"] = other_tenant.slug
        assert (await actor.get("/api/v1/auth/me")).json()["tenant"]["id"] == str(tenant.id)
        denied = await actor.get(f"/api/v1/admin/users/{b['id']}")
        assert denied.status_code in (403, 404)


async def test_duplicate_usernames_require_qualified_identity(app, owner, tenant, other_tenant):
    await tenant.create_user("sharedname", "player")
    await other_tenant.create_user("sharedname", "player")
    await configure(owner, tenant)
    async with client_for(app, ROOT) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "sharedname", "password": PASSWORD})
        assert r.status_code == 401
    async with login_on_shared(app, other_tenant, f"sharedname@{other_tenant.slug}") as c:
        assert (await c.get("/api/v1/auth/me")).json()["tenant"]["id"] == str(other_tenant.id)


async def test_admin_invitation_ownership_and_invitation_required(app, owner, tenant, other_tenant, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "register_ip_max_per_hour", 10000)
    await configure(owner, tenant, default_tenant_id=None)
    async with client_for(app, ROOT) as c:
        body = {"username": unique("direct"), "full_name": "Direct", "password": PASSWORD}
        assert (await c.post("/api/v1/auth/register", json=body)).status_code == 422
        body["ref"] = str(other_tenant.admin_id)
        r = await c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["user"]["tenant"]["id"] == str(other_tenant.id)
        assert r.json()["user"]["parent_id"] == str(other_tenant.admin_id)
    denied = await tenant.admin.put(f"{RENTAL}/settings", json={"shared_domain_enabled": False})
    assert denied.status_code == 401


async def test_suspension_blocks_only_owned_hierarchy(app, owner, tenant, other_tenant):
    a = await tenant.create_user(unique("player"), "player")
    b = await other_tenant.create_user(unique("player"), "player")
    await configure(owner, tenant)
    async with (
        login_on_shared(app, tenant, a["username"]) as ca,
        login_on_shared(app, other_tenant, b["username"]) as cb,
    ):
        r = await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/suspend", json={"reason": "Unpaid rental"})
        assert r.status_code == 200
        assert (await ca.get("/api/v1/auth/me")).status_code == 403
        assert (await cb.get("/api/v1/auth/me")).status_code == 200
        await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/activate", json={})
        assert (await ca.get("/api/v1/auth/me")).status_code == 200


async def test_rental_changes_do_not_rewrite_existing_invoices(owner, tenant, other_tenant):
    path = "/api/v1/platform/invoices"
    old = (await owner.client.get(path, params={"tenant_id": str(tenant.id)})).json()["items"]
    r = await owner.client.put(f"{RENTAL}/tenants/{tenant.id}/price", json={"monthly_cents": 1_500_000})
    assert r.status_code == 200, r.text
    summary = (await owner.client.get(f"{RENTAL}/tenants/{tenant.id}/summary")).json()
    assert summary["monthly_cents"] == 1_500_000
    assert summary["next_billing_at"]
    assert (await owner.client.get(path, params={"tenant_id": str(tenant.id)})).json()["items"] == old
    other = (await owner.client.get(f"{RENTAL}/tenants/{other_tenant.id}/summary")).json()
    assert other["monthly_cents"] != 1_500_000
    assert (await owner.client.put(f"{RENTAL}/tenants/{tenant.id}/price", json={"monthly_cents": -1})).status_code == 422


async def test_global_result_uses_separate_tenant_books_and_no_duplicate_credits(app, owner, tenant, other_tenant):
    event, fights = await open_card(tenant, commission_rate="0.1000")
    await enable(other_tenant, commission_rate="0.0850")
    p1 = await tenant.create_user(unique("player"), "player")
    from tests.test_commissions import me
    from tests.test_sub_agents import sub_network

    _, _, _, boss, agent, sub, c2 = await sub_network(other_tenant)
    p3 = await tenant.create_user(unique("player"), "player")
    p4 = await other_tenant.create_user(unique("player"), "player")
    judge = await other_tenant.create_user(unique("judge"), "declarator")
    c1 = await tenant.login(p1["username"])
    c3, c4 = await tenant.login(p3["username"]), await other_tenant.login(p4["username"])
    await configure(owner, tenant, global_event_id=event["id"])
    source = fights[0]["id"]
    target = str(projection_id(other_tenant.id, uuid.UUID(source)))
    c1.base_url = c2.base_url = c3.base_url = c4.base_url = f"http://{ROOT}"
    boss.base_url = agent.base_url = sub.base_url = f"http://{ROOT}"
    other_tenant.admin.base_url = f"http://{ROOT}"
    target_event = projection_id(other_tenant.id, uuid.UUID(event["id"]))
    assigned = await other_tenant.admin.put(f"/api/v1/declarator/events/{target_event}/assignments/{judge['id']}")
    assert assigned.status_code == 200, assigned.text
    await placed(c1, source, "meron", 100)
    await placed(c2, target, "meron", 200)
    await placed(c3, source, "wala", 100)
    await placed(c4, target, "wala", 600)
    first, second = await view(c1, source), await view(c2, target)
    assert pools(first)[0] == 100
    assert pools(second)[:2] == (200, 600)
    denied = await other_tenant.admin.post(f"/api/v1/admin/matches/{target}/betting/close")
    assert denied.status_code == 403
    # Existing source is scheduled; start it through the OWNER before declaration.
    started = await owner.client.post(f"{RENTAL}/fights/{source}/action", json={"action": "start"})
    assert started.status_code == 200, started.text
    result = await owner.client.post(f"{RENTAL}/fights/{source}/result", json={"outcome": "meron"})
    assert result.status_code == 200, result.text
    before = [(await view(c1, source))["wallet"], (await view(c2, target))["wallet"]]
    # Existing 3.66 odds pair: even pools pay 1.83; the second pool hits the 2.66 cap.
    assert before[0]["balance"] == "10083.00"
    assert before[1]["balance"] == "10332.00"
    for client, earned in ((boss, "7.00"), (agent, "4.00"), (sub, "6.00")):
        assert (await me(client))["total_earned"] == earned
    sync = await owner.client.post(f"{RENTAL}/synchronize")
    assert sync.status_code == 200, sync.text
    assert [(await view(c1, source))["wallet"], (await view(c2, target))["wallet"]] == before
    assert (await c1.get(f"/api/v1/portal/matches/{target}/book")).status_code == 404
    corrected = await owner.client.put(
        f"{RENTAL}/fights/{source}/result", json={"outcome": "draw", "reason": "Official correction"}
    )
    assert corrected.status_code == 200, corrected.text
    assert (await view(c1, source))["wallet"]["balance"] == "10000.00"
    assert (await view(c2, target))["wallet"]["balance"] == "10000.00"
    for client in (boss, agent, sub):
        assert (await me(client))["total_earned"] == "0.00"


async def test_global_stream_authorization_uses_viewers_own_balance(owner, tenant, other_tenant, monkeypatch):
    from app.modules.video import streams

    await enable(other_tenant, starting_balance="100")
    user = await other_tenant.create_user(unique("viewer"), "player")
    # Initialize the wallet through the same path used by the Player Dashboard.
    c = await other_tenant.login(user["username"])
    await c.get("/api/v1/portal/betting")
    async with platform_session() as db:
        stream = VideoStream(tenant_id=tenant.id, name="Global", path_id=uuid.uuid4().hex, source_kind="obs", enabled=True)
        db.add(stream)
        await db.flush()
        sid, path = str(stream.id), stream.path_id
    saved = await configure(owner, tenant, global_stream_id=sid)
    assert saved["obs_stream_key"]
    repeated = await configure(owner, tenant, global_stream_id=sid)
    assert repeated["obs_stream_key"] is None
    token, _ = streams.sign_playback(
        tenant_id=other_tenant.id,
        stream_id=uuid.UUID(sid),
        path_id=path,
        user_id=uuid.UUID(user["id"]),
        family=uuid.uuid4(),
        audience="player",
    )
    ok, reason = await streams._authorize_read({"protocol": "hls", "token": token}, path)
    assert ok, reason
    from tests.test_video_balance import set_balance

    await set_balance(other_tenant, user["id"], "99.99")
    assert not (await streams._authorize_read({"protocol": "hls", "token": token}, path))[0]
    await set_balance(other_tenant, user["id"], "100.00")
    assert (await streams._authorize_read({"protocol": "hls", "token": token}, path))[0]


async def test_shared_public_login_remains_available_when_default_tenant_is_suspended(app, owner, tenant):
    await configure(owner, tenant, default_tenant_id=None)
    await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/suspend", json={"reason": "Unpaid"})
    async with client_for(app, ROOT) as c:
        public = await c.get("/api/v1/public/tenant")
        assert public.status_code == 200, public.text
        assert public.json()["status"] == "active"
        assert public.json()["shared_player_domain"]
        assert not public.json()["allow_self_registration"]
        assert public.headers["cache-control"] == "private, no-store"


async def test_shared_accounts_cannot_use_a_tenant_specific_host(app, owner, tenant):
    await configure(owner, tenant)
    async with client_for(app, tenant.host) as c:
        assert (await c.get("/api/v1/public/tenant")).status_code == 404


async def test_future_activation_and_permanent_account_tenant(app, owner, tenant, other_tenant):
    from sqlalchemy.exc import IntegrityError

    from app.core.security import utcnow
    from app.modules.users.models import TenantUser

    user = await tenant.create_user(unique("scheduled"), "player")
    await configure(owner, tenant)
    tomorrow = utcnow() + timedelta(days=1)
    r = await owner.client.put(
        f"{RENTAL}/tenants/{tenant.id}/price", json={"monthly_cents": 1000000, "activation_at": tomorrow.isoformat()}
    )
    assert r.status_code == 200, r.text
    async with client_for(app, ROOT) as c:
        denied = await c.post("/api/v1/auth/login", json={"username": user["username"], "password": PASSWORD})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "subscription_not_active"
    with pytest.raises(IntegrityError, match="ownership is permanent"):
        async with platform_session() as db:
            row = await db.get(TenantUser, uuid.UUID(user["id"]))
            row.tenant_id = other_tenant.id
            await db.flush()


async def test_shared_websocket_keeps_finances_private_and_shares_only_stream_events(app, owner, tenant, other_tenant):
    import json

    from httpx_ws import aconnect_ws

    from app.modules.realtime.publisher import publish
    from tests.test_websocket import _receive, ws_client

    await configure(owner, tenant)
    token_a = tenant.admin.headers["Authorization"].split()[1]
    token_b = other_tenant.admin.headers["Authorization"].split()[1]
    async with ws_client(app, ROOT) as a, ws_client(app, ROOT) as b:
        async with aconnect_ws(f"/api/ws?token={token_a}", a) as wa, aconnect_ws(f"/api/ws?token={token_b}", b) as wb:
            assert (await _receive(wa))["type"] == "connected"
            assert (await _receive(wb))["type"] == "connected"
            await publish(tenant.id, "wallet.updated", {"balance": "1000"}, user_id=tenant.admin_id)
            assert (await _receive(wa))["type"] == "wallet.updated"
            await publish(tenant.id, "video.source_changed", {"stream_id": None})
            assert (await _receive(wa))["type"] == "video.source_changed"
            assert (await _receive(wb))["type"] == "video.source_changed"
            await wb.send_text(json.dumps({"type": "ping"}))
            assert (await _receive(wb))["type"] == "pong"


async def test_new_rental_joins_active_arena_without_a_player_subdomain(app, owner, tenant, plans):
    event, fights = await open_card(tenant)
    await configure(owner, tenant, global_event_id=event["id"])
    slug = unique("rental")
    created = await owner.client.post(
        "/api/v1/platform/tenants",
        json={
            "name": "New Rental",
            "slug": slug,
            "plan_id": str(plans["test-pro"].id),
            "admin_email": f"admin@{slug}.example",
            "admin_name": "New Admin",
            "with_trial": False,
        },
    )
    assert created.status_code == 201, created.text
    data = created.json()
    tid = uuid.UUID(data["tenant"]["id"])
    assert data["login_url"] == f"http://{ROOT}/admin/"
    assert data["tenant"]["primary_domain"] == ROOT
    assert data["login_identifier"] == f"admin@{slug}"
    domains = await owner.client.get(f"/api/v1/platform/tenants/{tid}/domains")
    assert domains.json() == []
    async with client_for(app, ROOT) as admin:
        logged = await admin.post(
            "/api/v1/auth/login", json={"username": data["login_identifier"], "password": data["temporary_password"]}
        )
        assert logged.status_code == 200, logged.text
        admin.headers["Authorization"] = f"Bearer {logged.json()['access_token']}"
        changed = await admin.post(
            "/api/v1/auth/me/password", json={"current_password": data["temporary_password"], "new_password": PASSWORD}
        )
        assert changed.status_code == 200, changed.text
        enabled = await admin.put("/api/v1/admin/betting/settings", json={"enabled": True, "starting_balance": "1000.00"})
        assert enabled.status_code == 200, enabled.text
        mid = str(projection_id(tid, uuid.UUID(fights[0]["id"])))
        book = await admin.get(f"/api/v1/admin/matches/{mid}/book")
        assert book.status_code == 200, book.text
        assert book.json()["state"] == "open"


async def test_activation_refuses_to_abandon_unsettled_legacy_bets(owner, tenant):
    await open_card(tenant)
    r = await owner.client.put(f"{RENTAL}/settings", json={"shared_domain_enabled": True, "broadcast_tenant_id": str(tenant.id)})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "legacy_bets_unsettled"
