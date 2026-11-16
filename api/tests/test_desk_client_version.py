"""An outdated Declarator desk build is refused before any work, so it cannot keep loading the server."""

from app.core.config import get_settings

URL = "/api/v1/declarator/events"


async def test_outdated_or_unversioned_desk_gets_426_and_the_current_one_is_served(tenant):
    missing = await tenant.admin.get(URL, headers={"X-ESB-Client": ""})
    assert missing.status_code == 426, missing.text
    body = missing.json()["error"]
    assert body["code"] == "client_outdated"
    assert body["details"] == {"minimum": get_settings().desk_min_client_version, "client": 0}
    old = await tenant.admin.get(URL, headers={"X-ESB-Client": "1"})
    assert old.status_code == 426
    current = await tenant.admin.get(URL)  # test clients send the current version, like the web app
    assert current.status_code != 426, current.text


async def test_gate_can_be_disabled(tenant, monkeypatch):
    monkeypatch.setattr(get_settings(), "desk_min_client_version", 0)
    unversioned = await tenant.admin.get(URL, headers={"X-ESB-Client": ""})
    assert unversioned.status_code != 426, unversioned.text


async def test_only_declarator_routes_are_gated(tenant):
    other = await tenant.admin.get("/api/v1/admin/events", headers={"X-ESB-Client": ""})
    assert other.status_code == 200, other.text
