"""Player transaction history and statement totals (read-only; simulated Arena Points, no cash value).

Real PostgreSQL. Covers bet outcomes on ledger rows (a lost bet has no ledger row of its own),
statement totals for wins, losses, refunds and corrections, date filters, authorization and tenant isolation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from tests.test_betting import ADMIN, PORTAL, START, balance, enable, placed, players
from tests.test_declarator import proposal, setup_desk
from tests.test_events import _match


async def rows(client, path=f"{PORTAL}/betting/entries", **params):  # type: ignore[no-untyped-def]
    r = await client.get(path, params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def statement(client, path=f"{PORTAL}/betting/statement", **params):  # type: ignore[no-untyped-def]
    r = await client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def open_book(tenant, match_id):  # type: ignore[no-untyped-def]
    assert (await tenant.admin.post(f"{ADMIN}/matches/{match_id}/betting/open")).status_code == 200


async def test_history_shows_wins_losses_refunds_and_statement_totals(tenant):
    await enable(tenant)
    _, event, fight, judge = await setup_desk(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    await open_book(tenant, fight["id"])
    await placed(ana, fight["id"], "meron", 100)
    await placed(ben, fight["id"], "wala", 100)
    # Fight #1: WALA wins (the Declarator's result is final).
    assert (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=proposal("wala"))).json()[
        "status"
    ] == "finalized"

    # Fight #2 is cancelled: the stake comes back as a refund.
    monitor = (await judge.get(f"/api/v1/declarator/events/{event['id']}/monitor")).json()
    second = monitor["matches"][-1]["id"]
    assert (await judge.post(f"/api/v1/declarator/matches/{second}/betting/open")).status_code == 200
    await placed(ana, second, "draw", 20)
    called_off = proposal("cancelled", reason="Fight called off")
    assert (await judge.post(f"/api/v1/declarator/matches/{second}/declarations", json=called_off)).status_code == 200

    # Ana's statement: the lost stake row says "lost", the cancelled one "refunded", plus the refund row.
    history = (await rows(ana))["items"]
    by_kind = {}
    for row in history:
        by_kind.setdefault(row["kind"], []).append(row)
    assert {r["username"] for r in history} == {"ana"}  # only her own rows
    lost = next(r for r in by_kind["stake"] if r["bet_side"] == "meron")
    assert (lost["bet_status"], D(lost["bet_amount"]), D(lost["bet_credited"])) == ("lost", D(100), D(0))
    refunded = next(r for r in by_kind["stake"] if r["bet_side"] == "draw")
    assert refunded["bet_status"] == "refunded"
    assert [D(r["delta"]) for r in by_kind["refund"]] == [D(20)] and by_kind["refund"][0]["bet_id"] == refunded["bet_id"]
    assert [r["kind"] for r in by_kind["grant"]] == ["grant"] and by_kind["grant"][0]["bet_id"] is None

    s = await statement(ana)
    assert (D(s["balance"]), D(s["granted"]), D(s["staked"])) == (START - 100, START, D(120))
    assert (D(s["winnings_credited"]), D(s["refunds"]), D(s["corrections"]), D(s["net_result"])) == (D(0), D(20), D(0), D(-100))
    assert (s["bets"], s["bets_won"], s["bets_lost"], s["bets_refunded"], s["bets_open"]) == (2, 0, 1, 1, 0)
    assert (D(s["lost_stakes"]), D(s["open_stakes"]), s["entries"]) == (D(100), D(0), 4)

    # Ben won: his 100 locked 1.83, so 183 is credited.
    s = await statement(ben)
    assert (D(s["winnings_credited"]), D(s["net_result"]), s["bets_won"], D(s["balance"])) == (D(183), D(83), 1, START + 83)
    won = next(r for r in (await rows(ben, kind="payout"))["items"])
    assert (won["bet_status"], won["bet_side"], D(won["delta"])) == ("won", "wala", D(183))
    assert D(s["balance"]) == await balance(ben)

    # The admin sees the same totals for any account in the tenant, and the same ledger rows.
    ana_id = history[0]["user_id"]
    assert await statement(tenant.admin, f"{ADMIN}/betting/statements/{ana_id}") == await statement(ana)
    admin_rows = (await rows(tenant.admin, f"{ADMIN}/betting/entries", user_id=ana_id))["items"]
    assert [r["id"] for r in admin_rows] == [r["id"] for r in history]


async def test_open_bets_and_date_filters(tenant):
    await enable(tenant)
    _, event, fight, _ = await setup_desk(tenant)
    [ana] = await players(tenant, "ana")
    await open_book(tenant, fight["id"])
    await placed(ana, fight["id"], "meron", 50)
    s = await statement(ana)
    assert (s["bets_open"], D(s["open_stakes"]), D(s["net_result"])) == (1, D(50), D(-50))
    [stake] = (await rows(ana, kind="stake"))["items"]
    assert stake["bet_status"] == "accepted"

    today = datetime.now(UTC).date()
    assert (await rows(ana, date_from=today.isoformat(), date_to=today.isoformat()))["total"] == 2
    yesterday = (today - timedelta(days=1)).isoformat()
    assert (await rows(ana, date_to=yesterday))["total"] == 0
    past = await statement(ana, date_to=yesterday)
    # Totals follow the range; the balance is always the current one.
    assert (past["entries"], past["bets"], D(past["staked"]), D(past["balance"])) == (0, 0, D(0), START - 50)
    assert (await rows(ana, date_from=(today + timedelta(days=1)).isoformat()))["total"] == 0


async def test_statement_access_is_own_or_ledger_viewer_and_tenant_scoped(tenant, other_tenant):
    await enable(tenant)
    [ana, ben] = await players(tenant, "ana", "ben")
    await balance(ana)  # creates the wallet and grant
    ana_id = (await rows(ana))["items"][0]["user_id"]
    # Players have no admin statement access and cannot ask the portal for someone else's ledger.
    assert (await ben.get(f"{ADMIN}/betting/statements/{ana_id}")).status_code == 403
    assert (await ben.get(f"{ADMIN}/betting/entries", params={"user_id": ana_id})).status_code == 403
    assert {r["username"] for r in (await rows(ben, user_id=ana_id))["items"]} <= {"ben"}
    assert (await statement(ben))["username"] == "ben"
    # Another tenant's administrator cannot read this tenant's account.
    assert (await other_tenant.admin.get(f"{ADMIN}/betting/statements/{ana_id}")).status_code == 404
    assert (await rows(other_tenant.admin, f"{ADMIN}/betting/entries", user_id=ana_id))["total"] == 0
    # An account without a wallet has an empty statement, not an error.
    s = await statement(ben)
    assert s["entries"] >= 0 and D(s["balance"]) >= 0


async def test_history_is_read_only_and_leaves_the_ledger_balanced(tenant):
    await enable(tenant)
    _, event, fight, _ = await setup_desk(tenant)
    [ana] = await players(tenant, "ana")
    await open_book(tenant, fight["id"])
    await placed(ana, fight["id"], "wala", 30)
    before = await rows(tenant.admin, f"{ADMIN}/betting/entries")
    for _ in range(3):
        await statement(ana)
        await rows(ana)
    after = await rows(tenant.admin, f"{ADMIN}/betting/entries")
    assert after == before
    recon = (await tenant.admin.get(f"{ADMIN}/betting/reconciliation")).json()
    assert recon["checks"]["wallets_match_ledger"] is True
    await _match(tenant, event["id"])  # unrelated card change does not alter the ledger
    assert (await rows(tenant.admin, f"{ADMIN}/betting/entries"))["total"] == before["total"]
