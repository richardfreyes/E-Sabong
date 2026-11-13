import asyncio
import uuid

from httpx_ws import aconnect_ws

from app.modules.declarator.settings import QuickBetSettings
from tests.test_betting import players
from tests.test_betting_staff import configure, token
from tests.test_declarator import setup_desk
from tests.test_websocket import _receive, ws_client

URL = "/api/v1/declarator/settings/quick-bets"
DEFAULTS = QuickBetSettings().model_dump(mode="json")


async def test_quick_bet_settings_persist_and_are_tenant_isolated(tenant, other_tenant):
    _, _, _, judge = await setup_desk(tenant)
    assert (await tenant.admin.get(URL)).json() == DEFAULTS
    custom = {"meron": ["7000.00", "8000.00", "15000.00", "30000.00"], "wala": DEFAULTS["wala"]}
    saved = await tenant.admin.put(URL, json=custom)
    assert saved.status_code == 200, saved.text
    assert saved.json() == custom
    assert (await judge.get(URL)).json() == custom
    assert (await tenant.admin.get(URL)).json() == custom
    assert (await other_tenant.admin.get(URL)).json() == DEFAULTS
    assert (await judge.put(URL, json=DEFAULTS)).status_code == 403
    [player] = await players(tenant, "settings_player")
    assert (await player.get(URL)).status_code == 403
    assert (await player.put(URL, json=DEFAULTS)).status_code == 403
    assert (await tenant.admin.put(URL, json=DEFAULTS)).status_code == 200
    assert (await judge.get(URL)).json() == DEFAULTS


async def test_quick_bet_settings_validate_amounts_and_shape(tenant):
    for amounts in [
        [0, 10000, 20000, 50000],
        [-1, 10000, 20000, 50000],
        ["1.001", 10000, 20000, 50000],
        ["NaN", 10000, 20000, 50000],
        ["Infinity", 10000, 20000, 50000],
        [1, 2, 3],
        [1, 2, 3, 4, 5],
    ]:
        response = await tenant.admin.put(URL, json={"meron": amounts, "wala": DEFAULTS["wala"]})
        assert response.status_code == 422, response.text
    assert (await tenant.admin.get(URL)).json() == DEFAULTS


async def test_settings_notify_declarators_and_custom_amount_is_exact(app, tenant):
    await configure(tenant, starting_balance="1000.00")
    _, _, fight, judge = await setup_desk(tenant)
    custom = {**DEFAULTS, "meron": ["7000.00", "8000.00", "15000.00", "30000.00"]}
    async with ws_client(app, tenant.host) as client:
        async with aconnect_ws(f"/api/ws?token={token(judge)}", client) as socket:
            await _receive(socket)
            await asyncio.sleep(0.2)
            assert (await tenant.admin.put(URL, json=custom)).status_code == 200
            frame = await _receive(socket)
            assert frame["type"] == "declarator.settings_updated"
    assert (await judge.get(URL)).json() == custom
    base = f"/api/v1/declarator/matches/{fight['id']}"
    assert (await judge.post(base + "/betting/open")).status_code == 200
    body = {"request_id": str(uuid.uuid4()), "side": "meron", "amount": "7000.00"}
    added = await judge.post(base + "/dummy-bets", json=body)
    assert added.status_code == 200, added.text
    assert added.json()["pools"]["meron"] == "7000.00"
    assert added.json()["pools"]["wala"] == "0.00"
    assert (await judge.post(base + "/dummy-bets", json=body)).json()["pools"]["meron"] == "7000.00"
