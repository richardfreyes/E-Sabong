"""Owner prices isolate tenants; bets record their acceptance price and are paid the board price at close."""

from decimal import Decimal as D

from app.core.config import get_settings
from tests.test_betting import declare, open_card, placed, players, shown, view


def endpoint(tenant):
    return f"/api/v1/platform/tenants/{tenant.id}/betting-odds"


async def save(owner, tenant, meron, wala):
    response = await owner.client.put(endpoint(tenant), json={"meron": meron, "wala": wala})
    assert response.status_code == 200, response.text
    return response.json()


async def test_owner_odds_are_independent_persistent_validated_and_authorized(owner, tenant, other_tenant, monkeypatch):
    assert (await save(owner, tenant, 180, 190))["configured"]
    await save(owner, other_tenant, 185, 185)
    assert (await owner.client.get(endpoint(tenant))).json()["meron"] == 180
    assert (await owner.client.get(endpoint(other_tenant))).json()["meron"] == 185
    assert (await tenant.admin.put(endpoint(tenant), json={"meron": 200, "wala": 200})).status_code in (401, 404)
    for value in (99, 301, 183.5, "185", None):
        assert (await owner.client.put(endpoint(tenant), json={"meron": value, "wala": 183})).status_code == 422
    monkeypatch.setattr(get_settings(), "owner_odds_max", 190)
    assert (await owner.client.put(endpoint(tenant), json={"meron": 191, "wala": 183})).status_code == 422
    assert (await owner.client.get(endpoint(tenant))).json()["meron"] == 180


async def test_bets_record_their_price_but_pay_the_board_price_at_close(owner, tenant):
    await save(owner, tenant, 183, 183)
    _, [fight] = await open_card(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    first = await placed(ana, fight["id"], "meron", 100)
    assert D(first["bet"]["multiplier"]) == D("1.83")  # recorded, not what it is paid
    await placed(ben, fight["id"], "wala", 1000)
    await save(owner, tenant, 180, 190)
    second = await placed(ana, fight["id"], "meron", 100)
    assert D(second["bet"]["multiplier"]) == D("1.80")
    current = await view(ana, fight["id"])
    assert shown(current) == (D("1.80"), D("1.90"))
    assert current["book"]["odds"]["source"] == "owner"  # fixed by the Owner: it does not move with the pools
    # Pays is the stake × the odds on the board: 200 × 1.80 = 360 (not 100 × 1.83 + 100 × 1.80).
    assert D(current["mine"]["meron"]["pays"]) == D("360.00")
    await declare(tenant, fight["id"], "meron")  # closes at 1.80
    settled = await view(ana, fight["id"])
    assert D(settled["mine"]["meron"]["pays"]) == D("360.00")
    # A later price change cannot rewrite a closed fight's price or a settled payout.
    await save(owner, tenant, 200, 200)
    assert D((await view(ana, fight["id"]))["mine"]["meron"]["pays"]) == D("360.00")


async def test_reset_restores_default_logo_and_prices_atomically(owner, tenant):
    from tests.test_owner_branding import upload

    await save(owner, tenant, 180, 190)
    assert (await upload(owner, tenant)).json()["logo_url"]
    response = await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/branding-betting/reset")
    assert response.status_code == 200, response.text
    assert response.json()["meron"] == response.json()["wala"] == 183
    detail = (await owner.client.get(f"/api/v1/platform/tenants/{tenant.id}")).json()
    assert detail["branding"]["logo_url"] is None
