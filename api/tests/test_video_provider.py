from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import tenant_session
from app.core.redis import get_redis
from app.modules.video.models import VideoProvider
from tests.test_declarator import setup_desk
from tests.test_events import _event


async def reset_attempt(tenant):
    await get_redis().delete(f"video:attempt:{tenant.id}")


async def test_configuration_credentials_and_isolation(tenant, other_tenant, monkeypatch):
    monkeypatch.setenv("VIDEO_CREDENTIALS_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    path = "/api/v1/admin/settings/video-provider"
    body = {"enabled": True, "adapter": "demo", "username": "private-user", "password": "private-pass"}
    r = await tenant.admin.put(path, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["credentials_saved"]
    assert "private-" not in r.text and "password" not in r.json()
    assert not (await other_tenant.admin.get(path)).json()["credentials_saved"]
    async with tenant_session(tenant.id) as db:
        row = await db.scalar(select(VideoProvider).where(VideoProvider.tenant_id == tenant.id))
        original = row.credentials
        assert "private-" not in original
    assert (await tenant.admin.put(path, json={"enabled": True})).status_code == 200
    async with tenant_session(tenant.id) as db:
        assert (await db.scalar(select(VideoProvider).where(VideoProvider.tenant_id == tenant.id))).credentials == original
    assert (await tenant.admin.put(path, json={"clear_credentials": True})).json()["credentials_saved"] is False
    assert (
        await tenant.admin.put(path, json={"adapter": "json-bearer-v1", "api_base_url": "http://127.0.0.1"})
    ).status_code == 422
    get_settings.cache_clear()


async def test_playback_assignment_refresh_and_official_results_unchanged(tenant, other_tenant):
    _, event, match, judge = await setup_desk(tenant)
    path = "/api/v1/admin/settings/video-provider"
    assert (await tenant.admin.put(path, json={"enabled": True, "event_id": event["id"]})).status_code == 200
    await tenant.create_user("viewer", "player")
    from tests.test_betting_staff import configure

    await configure(tenant, starting_balance="1000.00")
    viewer = await tenant.login("viewer")
    assert (await viewer.get("/api/v1/portal/betting")).status_code == 200
    assert (await viewer.get(path)).status_code == 403
    url = f"/api/v1/video/events/{event['id']}"
    assert (await viewer.post(url + "/playback")).json()["playback"]["type"] == "mp4"
    assert (await other_tenant.admin.post(url + "/playback")).status_code == 404
    for action in ["refresh", "reconnect", "event"]:
        await reset_attempt(tenant)
        result = await judge.post(url + f"/{action}?audience=declarator")
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "demo"
    assert (await viewer.post(url + "/event?audience=declarator")).status_code == 403
    assert (await viewer.post(url + "/event")).status_code == 403
    foreign = await _event(other_tenant)
    assert (await judge.post(f"/api/v1/video/events/{foreign['id']}/refresh?audience=declarator")).status_code == 404
    official = (await viewer.get(f"/api/v1/portal/matches/{match['id']}")).json()
    assert official["result"] is None and official["status"] == "in_progress"
    assert (await tenant.admin.put(path, json={"enabled": False})).status_code == 200
    await reset_attempt(tenant)
    assert (await viewer.post(url + "/playback")).json()["status"] == "disabled"


async def test_production_refuses_the_demonstration_adapter(tenant, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(type(settings), "is_production", property(lambda _: True))
    path = "/api/v1/admin/settings/video-provider"
    assert (await tenant.admin.get(path)).json()["demo_available"] is False
    r = await tenant.admin.put(path, json={"enabled": True, "adapter": "demo"})
    assert r.status_code == 422 and "not available in production" in r.text
    # Saving it disabled (e.g. turning a legacy demo source off) stays possible.
    assert (await tenant.admin.put(path, json={"enabled": False, "adapter": "demo"})).status_code == 200
