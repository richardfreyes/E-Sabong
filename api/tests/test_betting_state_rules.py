"""Betting opens only on a started fight, on every route (Admin, Owner-run admin, Declarator desk)."""

from tests.test_betting import ADMIN, enable
from tests.test_predictions import live_card


async def test_admin_cannot_open_betting_on_an_unstarted_fight(tenant):
    await enable(tenant)
    _, [fight] = await live_card(tenant, 1)
    refused = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "fight_not_started"
    # Nothing was created: no book, so no player can stake on the unstarted fight.
    book = await tenant.admin.get(f"{ADMIN}/matches/{fight['id']}/book")
    assert book.status_code in (200, 404), book.text
    if book.status_code == 200:
        assert book.json().get("state") in (None, "none")
    started = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/start")
    assert started.status_code == 200, started.text
    opened = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")
    assert opened.status_code == 200, opened.text
    assert opened.json()["state"] == "open"
