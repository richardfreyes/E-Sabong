"""Shared-host identities, rental billing and independent global-fight settlement."""

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update

from app.core.database import platform_session
from app.modules.tenants.models import Tenant
from app.modules.tenants.rental import RentalSettings
from app.modules.video.models import VideoStream
from tests.conftest import PASSWORD, client_for, unique
from tests.streaming_fixtures import streaming_tenant
from tests.test_betting import enable, open_card, placed, pools, view

tenant = streaming_tenant
pytestmark = pytest.mark.isolated_history

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
    assert denied.status_code == 404


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


async def _desk_card(admin, judge_id, name):
    created = await admin.post(
        "/api/v1/admin/events", json={"name": name, "venue": "Arena", "starts_at": "2030-05-01T09:00:00+08:00"}
    )
    assert created.status_code == 201, created.text
    event = created.json()
    fight = await admin.post(f"/api/v1/admin/events/{event['id']}/matches", json={"meron_name": "Red", "wala_name": "Blue"})
    assert fight.status_code == 201, fight.text
    for path in (f"events/{event['id']}/publish", f"events/{event['id']}/start", f"matches/{fight.json()['id']}/start"):
        r = await admin.post("/api/v1/admin/" + path)
        assert r.status_code == 200, r.text
    assigned = await admin.put(f"/api/v1/declarator/events/{event['id']}/assignments/{judge_id}")
    assert assigned.status_code == 200, assigned.text
    return event, fight.json()


async def test_each_workspace_runs_its_own_card_and_declarator_in_shared_mode(app, owner, tenant, other_tenant):
    await enable(tenant)
    await enable(other_tenant)
    accounts = []
    for t in (tenant, other_tenant):
        player = await t.create_user(unique("player"), "player")
        judge = await t.create_user(unique("judge"), "declarator")
        accounts.append((judge, await t.login(player["username"]), await t.login(judge["username"])))
    (judge_a, player_a, desk_a), (judge_b, player_b, desk_b) = accounts
    rival = await tenant.create_user(unique("player"), "player")
    rival_a = await tenant.login(rival["username"])
    await configure(owner, tenant)
    for c in (tenant.admin, other_tenant.admin, player_a, rival_a, player_b, desk_a, desk_b):
        c.base_url = f"http://{ROOT}"

    # Every Admin creates and runs its own card; nothing is copied between workspaces.
    event_a, fight_a = await _desk_card(tenant.admin, judge_a["id"], "Arena A")
    event_b, fight_b = await _desk_card(other_tenant.admin, judge_b["id"], "Arena B")
    for c, own, other in ((player_a, event_a, event_b), (player_b, event_b, event_a)):
        listed = await c.get("/api/v1/portal/events")
        assert listed.status_code == 200, listed.text
        ids = {e["id"] for e in listed.json()["items"]}
        assert own["id"] in ids and other["id"] not in ids
        assert (await c.get(f"/api/v1/portal/events/{other['id']}")).status_code == 404

    # Each workspace's own Declarator controls betting and results for its fights only.
    for desk, fight in ((desk_a, fight_a), (desk_b, fight_b)):
        opened = await desk.post(f"/api/v1/declarator/matches/{fight['id']}/betting/open")
        assert opened.status_code == 200, opened.text
    assert (await desk_b.post(f"/api/v1/declarator/matches/{fight_a['id']}/betting/close")).status_code in (403, 404)
    await placed(player_a, fight_a["id"], "meron", 100)
    await placed(rival_a, fight_a["id"], "wala", 100)
    await placed(player_b, fight_b["id"], "meron", 200)
    assert (await player_a.get(f"/api/v1/portal/matches/{fight_b['id']}/book")).status_code == 404
    assert pools(await view(player_a, fight_a["id"]))[0] == 100
    assert pools(await view(player_b, fight_b["id"]))[0] == 200

    # Different results per workspace settle only that workspace's books.
    for desk, fight, outcome in ((desk_a, fight_a, "meron"), (desk_b, fight_b, "wala")):
        closed = await desk.post(f"/api/v1/declarator/matches/{fight['id']}/betting/close")
        assert closed.status_code == 200, closed.text
        declared = await desk.post(
            f"/api/v1/declarator/matches/{fight['id']}/declarations", json={"request_id": str(uuid.uuid4()), "outcome": outcome}
        )
        assert declared.status_code == 200, declared.text
    won = (await view(player_a, fight_a["id"]))["wallet"]["balance"]
    lost = (await view(player_b, fight_b["id"]))["wallet"]["balance"]
    assert float(won) > 10000
    assert (await view(rival_a, fight_a["id"]))["wallet"]["balance"] == "9900.00"
    assert lost == "9800.00"


async def test_global_stream_authorization_uses_viewers_own_balance(owner, tenant, other_tenant, monkeypatch):
    from app.modules.video import streams

    await enable(other_tenant, starting_balance="100")
    user = await other_tenant.create_user(unique("viewer"), "player")
    # Initialize the wallet through the same path used by the Player Dashboard.
    c = await other_tenant.login(user["username"])
    await c.get("/api/v1/portal/betting")
    async with platform_session() as db:
        provider_id = await db.scalar(select(Tenant.id).where(Tenant.is_global_stream_provider))
        await db.execute(update(VideoStream).where(VideoStream.tenant_id == provider_id).values(is_default=False))
        stream = VideoStream(
            tenant_id=provider_id, name="Global", path_id=streams.new_path_id(), source_kind="obs", enabled=True, is_default=True
        )
        db.add(stream)
        await db.flush()
        sid, path = str(stream.id), stream.path_id
    saved = await configure(owner, tenant)
    assert "obs_stream_key" not in saved and "global_stream_id" not in saved
    assert (await owner.client.put(f"{RENTAL}/settings", json={"global_stream_id": sid})).status_code == 422
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
            async with platform_session() as db:
                provider_id = await db.scalar(select(Tenant.id).where(Tenant.is_global_stream_provider))
            await publish(provider_id, "video.source_changed", {"stream_id": None})
            assert (await _receive(wa))["type"] == "video.source_changed"
            assert (await _receive(wb))["type"] == "video.source_changed"
            await wb.send_text(json.dumps({"type": "ping"}))
            assert (await _receive(wb))["type"] == "pong"


async def test_new_rental_runs_its_own_events_without_a_player_subdomain(app, owner, tenant, plans):
    await open_card(tenant)
    await configure(owner, tenant)
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
        # Nothing from another workspace is copied in; the new Admin starts with an empty card.
        events = await admin.get("/api/v1/admin/events")
        assert events.status_code == 200, events.text
        assert events.json()["items"] == []
        created = await admin.post("/api/v1/admin/events", json={"name": "Own derby", "starts_at": "2030-05-01T09:00:00+08:00"})
        assert created.status_code == 201, created.text
        assert [e["name"] for e in (await admin.get("/api/v1/admin/events")).json()["items"]] == ["Own derby"]


async def test_enabling_shared_mode_keeps_running_tenant_cards(app, owner, tenant):
    event, fights = await open_card(tenant)
    player = await tenant.create_user(unique("player"), "player")
    c = await tenant.login(player["username"])
    await configure(owner, tenant)
    c.base_url = f"http://{ROOT}"
    assert (await view(c, fights[0]["id"]))["book"]["state"] == "open"
    assert (await c.get(f"/api/v1/portal/events/{event['id']}")).status_code == 200
