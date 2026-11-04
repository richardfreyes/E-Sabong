"""Balance Management (/admin/balance): role-scoped views and administrator operations on the single
play-balance ledger. Real PostgreSQL.

Covers tenant isolation, role and hierarchy scope, uniform starting balances and grants, maker-checker
corrections, insufficient balance, idempotency, concurrency, rollback, reconciliation, history and the
database guards. Simulated balances only: nothing here has cash value or moves between accounts.
"""

import asyncio
import uuid
from decimal import Decimal as D

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import tenant_session
from tests.test_betting import ADMIN, PORTAL, START, balance, bet, declare, enable, open_card, placed

BAL = f"{ADMIN}/balance"


@pytest.fixture(scope="module", autouse=True)
async def discard_balance_operation_rows():
    """Migration 0018 refuses to downgrade with balance history, as in production. Later migration
    rehearsals in this disposable database need it empty, so the module drops its own rows."""
    yield
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    url = get_settings().database_url_migrations
    assert url.rsplit("/", 1)[-1].split("?")[0].startswith("esabong_test")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE balance_operations, play_wallet_entries"))
    finally:
        await engine.dispose()


def key() -> str:
    return f"test-{uuid.uuid4().hex}"


async def grant(client, amount, idem=None, **extra):  # type: ignore[no-untyped-def]
    return await client.post(f"{BAL}/grants", json={"amount": str(amount), "idempotency_key": idem or key(), **extra})


async def add_to(client, user_id, amount, idem=None, **extra):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{BAL}/account-grants",
        json={"target_user_id": user_id, "amount": str(amount), "idempotency_key": idem or key(), **extra},
    )


async def ok(response, code=200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


async def items(client, path, **params):  # type: ignore[no-untyped-def]
    return (await ok(await client.get(f"{BAL}/{path}", params={"page_size": 100, **params})))["items"]


async def second_admin(tenant):  # type: ignore[no-untyped-def]
    name = f"adm{uuid.uuid4().hex[:6]}"
    await tenant.create_user(name, "admin")
    return await tenant.login(name)


async def hierarchy(tenant):  # type: ignore[no-untyped-def]
    """master -> agent -> (p1, p2); a second agent branch with p3; p4 directly in the tenant."""
    ma = await tenant.create_user("mast", "master_agent")
    ag = await tenant.create_user("agt", "agent", parent_id=ma["id"])
    ag2 = await tenant.create_user("agt2", "agent", parent_id=ma["id"])
    ids = {"mast": ma["id"], "agt": ag["id"], "agt2": ag2["id"]}
    for name, parent in (("pl1", ag["id"]), ("pl2", ag["id"]), ("pl3", ag2["id"]), ("pl4", None)):
        ids[name] = (await tenant.create_user(name, "player", parent_id=parent))["id"]
    return ids


async def reconciled(tenant):  # type: ignore[no-untyped-def]
    rec = await ok(await tenant.admin.get(f"{ADMIN}/betting/reconciliation"))
    assert rec["balanced"] is True, rec
    assert rec["checks"]["wallet_mismatches"] == 0
    granted = sum(D(rec[k]) for k in ("granted", "account_granted", "uniform_granted", "reset_net", "corrections_net"))
    assert granted == D(rec["in_wallets"]) + D(rec["at_stake"]) + D(rec["house_result"])
    return rec


async def admin_balance(tenant, user_id) -> D:  # type: ignore[no-untyped-def]
    return D((await ok(await tenant.admin.get(f"{BAL}/accounts/{user_id}")))["account"]["balance"])


# ---- starting balance and uniform grants ----


async def test_uniform_grant_credits_every_active_player_the_same_amount_once(tenant):
    await enable(tenant)
    ids = await hierarchy(tenant)
    p1 = await tenant.login("pl1")
    assert await balance(p1) == START  # starting balance on first use
    suspended = ids["pl4"]
    assert (await tenant.admin.post(f"{ADMIN}/users/{suspended}/suspend")).status_code == 200

    idem = key()
    op = await ok(await grant(tenant.admin, "250.50", idem, remarks="Season two kick-off"), 201)
    assert (op["kind"], op["status"], D(op["amount"])) == ("uniform_grant", "applied", D("250.50"))
    assert op["reference"].startswith("BAL-") and op["created_by_username"] == "admin"
    # p1, p2, p3 are the active players; agents, admins and the suspended player get nothing.
    assert op["accounts_affected"] == 3 and D(op["total_delta"]) == D("751.50")
    for name in ("pl1", "pl2", "pl3"):
        assert await admin_balance(tenant, ids[name]) == START + D("250.50")
    assert await admin_balance(tenant, suspended) == 0
    assert await admin_balance(tenant, ids["agt"]) == 0

    # Same key + same body replays (200, nothing posted twice); same key + other body conflicts.
    again = await ok(await grant(tenant.admin, "250.50", idem, remarks="Season two kick-off"))
    assert again["id"] == op["id"] and again["replayed"] is True
    r = await grant(tenant.admin, "999", idem)
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"
    assert await admin_balance(tenant, ids["pl1"]) == START + D("250.50")

    # History: one row per player, carrying the reference, previous and resulting balance, remarks.
    rows = await items(tenant.admin, "entries", operation_id=op["id"])
    assert sorted(r["username"] for r in rows) == ["pl1", "pl2", "pl3"]
    for row in rows:
        assert row["kind"] == "uniform_grant" and row["operation_reference"] == op["reference"]
        assert D(row["balance_after"]) - D(row["balance_before"]) == D("250.50") == D(row["delta"])
        assert row["operation_remarks"] == "Season two kick-off" and row["operation_status"] == "applied"
    detail = await ok(await tenant.admin.get(f"{BAL}/entries/{rows[0]['id']}"))
    assert detail == rows[0]

    statement = await ok(await p1.get(f"{PORTAL}/betting/statement"))
    assert (D(statement["granted"]), D(statement["uniform_grants"]), D(statement["balance"])) == (
        START,
        D("250.50"),
        START + D("250.50"),
    )
    mine = (await ok(await p1.get(f"{PORTAL}/betting/entries", params={"kind": "uniform_grant"})))["items"]
    assert [r["operation_reference"] for r in mine] == [op["reference"]]
    await reconciled(tenant)

    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "balance.uniform_grant"})).json()["items"]
    assert len(logs) == 1 and logs[0]["changes"]["reference"] == op["reference"]


async def test_account_grant_adds_balance_only_to_the_selected_player(tenant):
    await enable(tenant)
    ids = await hierarchy(tenant)
    idem = key()
    op = await ok(await add_to(tenant.admin, ids["pl1"], "40.25", idem, remarks="Opening top-up"), 201)
    assert (op["kind"], op["status"], op["target_username"], D(op["amount"]), op["accounts_affected"]) == (
        "account_grant",
        "applied",
        "pl1",
        D("40.25"),
        1,
    )
    assert D(op["total_delta"]) == D("40.25")
    assert await admin_balance(tenant, ids["pl1"]) == START + D("40.25")
    for name in ("pl2", "pl3", "pl4", "agt", "mast"):
        assert await admin_balance(tenant, ids[name]) == 0

    again = await ok(await add_to(tenant.admin, ids["pl1"], "40.25", idem, remarks="Opening top-up"))
    assert again["id"] == op["id"] and again["replayed"] is True
    conflict = await add_to(tenant.admin, ids["pl2"], "40.25", idem, remarks="Opening top-up")
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    assert await admin_balance(tenant, ids["pl1"]) == START + D("40.25")

    rows = await items(tenant.admin, "entries", user_id=ids["pl1"])
    assert sorted(r["kind"] for r in rows) == ["account_grant", "grant"]
    added = next(r for r in rows if r["kind"] == "account_grant")
    assert added["operation_reference"] == op["reference"] and D(added["delta"]) == D("40.25")
    statement = await ok(await (await tenant.login("pl1")).get(f"{PORTAL}/betting/statement"))
    assert (D(statement["granted"]), D(statement["account_grants"]), D(statement["balance"])) == (
        START,
        D("40.25"),
        START + D("40.25"),
    )

    # A different player, an agent, a suspended player, and a non-administrator are refused.
    unknown = await add_to(tenant.admin, str(uuid.uuid4()), "5")
    assert unknown.status_code == 404
    agent_target = await add_to(tenant.admin, ids["agt"], "5")
    assert agent_target.status_code == 404
    assert (await tenant.admin.post(f"{ADMIN}/users/{ids['pl4']}/suspend")).status_code == 200
    inactive = await add_to(tenant.admin, ids["pl4"], "5")
    assert inactive.status_code == 409 and inactive.json()["error"]["code"] == "not_active"
    assert await admin_balance(tenant, ids["pl4"]) == 0
    refused = await add_to(await tenant.login("agt"), ids["pl2"], "5")
    assert refused.status_code == 403
    extra = await tenant.admin.post(
        f"{BAL}/account-grants",
        json={"target_user_id": ids["pl2"], "amount": "5", "idempotency_key": key(), "balance": "1"},
    )
    assert extra.status_code == 422

    # The posting can be reversed, and only by a different administrator.
    checker = await second_admin(tenant)
    proposed = await ok(await tenant.admin.post(f"{BAL}/corrections", json=correction(ids["pl1"], entry_id=added["id"])), 201)
    assert (proposed["direction"], D(proposed["amount"])) == ("debit", D("40.25"))
    assert await admin_balance(tenant, ids["pl1"]) == START + D("40.25")
    approved = await ok(await checker.post(f"{BAL}/corrections/{proposed['id']}/approve"))
    assert approved["status"] == "applied"
    assert await admin_balance(tenant, ids["pl1"]) == START
    await reconciled(tenant)


async def test_grant_gives_a_never_used_wallet_its_starting_balance_first(tenant):
    await enable(tenant)
    pid = (await tenant.create_user("fresh", "player"))["id"]
    op = await ok(await grant(tenant.admin, 100), 201)
    assert await admin_balance(tenant, pid) == START + 100
    kinds = sorted(r["kind"] for r in await items(tenant.admin, "entries", user_id=pid))
    assert kinds == ["grant", "uniform_grant"]
    assert op["accounts_affected"] == 1
    await reconciled(tenant)


async def test_grant_validation_never_trusts_client_fields(tenant):
    await enable(tenant)
    await tenant.create_user("val", "player")
    for body in (
        {"amount": "-5", "idempotency_key": key()},
        {"amount": "0", "idempotency_key": key()},
        {"amount": "1000000.01", "idempotency_key": key()},
        {"amount": "1.001", "idempotency_key": key()},
        {"amount": "5", "idempotency_key": "short"},
        {"amount": "5", "idempotency_key": key(), "tenant_id": str(uuid.uuid4())},
        {"amount": "5", "idempotency_key": key(), "target_user_id": str(uuid.uuid4())},
        {"amount": "5", "idempotency_key": key(), "balance": "1"},
    ):
        r = await tenant.admin.post(f"{BAL}/grants", json=body)
        assert r.status_code == 422, (body, r.text)
    assert await items(tenant.admin, "operations") == []


async def test_failed_grant_leaves_every_balance_unchanged(tenant):
    # Players near the maximum balance make the whole grant fail: nobody is credited, nothing is kept
    # (not even the starting balances the grant would have posted first).
    await enable(tenant, starting_balance="999999999000.00")
    ids = [(await tenant.create_user(n, "player"))["id"] for n in ("full", "full2")]
    r = await grant(tenant.admin, 1000)
    assert r.status_code == 409 and r.json()["error"]["code"] == "balance_limit"
    for pid in ids:
        account = (await ok(await tenant.admin.get(f"{BAL}/accounts/{pid}")))["account"]
        assert (D(account["balance"]), account["has_wallet"]) == (0, False)
    assert await items(tenant.admin, "entries") == []
    assert await items(tenant.admin, "operations") == []


# ---- role authorization, hierarchy scope and tenant isolation ----


async def test_roles_see_only_their_scope_and_only_admins_write(tenant):
    await enable(tenant)
    ids = await hierarchy(tenant)
    ma, ag, p1 = await tenant.login("mast"), await tenant.login("agt"), await tenant.login("pl1")
    await balance(p1)
    op = await ok(await grant(tenant.admin, 10), 201)

    names = lambda rows: sorted(r["username"] for r in rows)  # noqa: E731
    assert names(await items(tenant.admin, "accounts")) == ["agt", "agt2", "mast", "pl1", "pl2", "pl3", "pl4"]
    assert names(await items(ma, "accounts")) == ["agt", "agt2", "pl1", "pl2", "pl3"]
    assert names(await items(ma, "accounts", account_type="player")) == ["pl1", "pl2", "pl3"]
    assert names(await items(ma, "accounts", parent_id=ids["agt"])) == ["pl1", "pl2"]
    assert names(await items(ma, "accounts", q="pl3")) == ["pl3"]
    assert names(await items(ag, "accounts")) == ["pl1", "pl2"]
    # A parent filter outside the caller's scope reveals nothing.
    assert await items(ag, "accounts", parent_id=ids["agt2"]) == []

    assert {r["username"] for r in await items(ag, "entries")} == {"pl1", "pl2"}
    assert {r["username"] for r in await items(ma, "entries")} == {"pl1", "pl2", "pl3"}
    assert await items(ag, "entries", user_id=ids["pl3"]) == []
    p3_entry = (await items(tenant.admin, "entries", user_id=ids["pl3"]))[0]["id"]
    for path in (f"accounts/{ids['pl3']}", f"entries/{p3_entry}", f"accounts/{ids['mast']}"):
        r = await ag.get(f"{BAL}/{path}")
        assert r.status_code == 404, (path, r.text)
    detail = await ok(await ma.get(f"{BAL}/accounts/{ids['pl3']}"))
    assert D(detail["statement"]["uniform_grants"]) == 10

    s = await ok(await ag.get(f"{BAL}/summary"))
    assert (s["accounts"], s["participants"], s["pending_corrections"], s["can_manage"]) == (2, 2, None, False)
    s = await ok(await tenant.admin.get(f"{BAL}/summary"))
    assert (s["accounts"], s["participants"], s["pending_corrections"], s["can_manage"]) == (7, 4, 0, True)

    for client in (ma, ag, p1):
        assert (await grant(client, 5)).status_code == 403
        r = await client.post(f"{BAL}/resets", json={"idempotency_key": key()})
        assert r.status_code == 403
        body = {"target_user_id": ids["pl1"], "operation_id": op["id"], "remarks": "missed it", "idempotency_key": key()}
        assert (await client.post(f"{BAL}/corrections", json=body)).status_code == 403
    for client in (ma, ag):
        assert (await client.get(f"{BAL}/operations")).status_code == 403
        assert (await client.get(f"{BAL}/operations/{op['id']}")).status_code == 403
    for path in ("summary", "accounts", "entries", "operations"):
        assert (await p1.get(f"{BAL}/{path}")).status_code == 403
    # Players keep their own read-only history and statement.
    assert {r["username"] for r in (await ok(await p1.get(f"{PORTAL}/betting/entries")))["items"]} == {"pl1"}


async def test_tenants_are_isolated(tenant, other_tenant):
    await enable(tenant)
    await enable(other_tenant)
    mine = (await tenant.create_user("iso", "player"))["id"]
    theirs = (await other_tenant.create_user("iso", "player"))["id"]
    op = await ok(await grant(tenant.admin, 77), 201)
    entry = (await items(tenant.admin, "entries", operation_id=op["id"]))[0]["id"]

    other = other_tenant.admin
    for path in (f"accounts/{mine}", f"entries/{entry}", f"operations/{op['id']}"):
        assert (await other.get(f"{BAL}/{path}")).status_code == 404, path
    assert [a["id"] for a in await items(other, "accounts")] == [theirs]
    assert await items(other, "entries", user_id=mine) == []
    assert await items(other, "operations") == []
    body = {"target_user_id": mine, "entry_id": entry, "remarks": "cross-tenant", "idempotency_key": key()}
    assert (await other.post(f"{BAL}/corrections", json=body)).status_code == 404
    second = await second_admin(other_tenant)
    assert (await second.post(f"{BAL}/corrections/{op['id']}/approve")).status_code == 404

    await ok(await grant(other, 5), 201)
    assert await admin_balance(tenant, mine) == START + 77
    assert await admin_balance(other_tenant, theirs) == START + 5
    # A token for one tenant is never accepted on another tenant's host.
    other.headers["Authorization"] = tenant.admin.headers["Authorization"]
    assert (await other.get(f"{BAL}/summary")).status_code == 401
    await reconciled(tenant)


# ---- corrections (maker-checker) ----


async def _grant_entry(tenant, user_id, amount=100):  # type: ignore[no-untyped-def]
    op = await ok(await grant(tenant.admin, amount), 201)
    [row] = await items(tenant.admin, "entries", operation_id=op["id"], user_id=user_id)
    return op, row


def correction(user_id, remarks="Duplicate grant issued in error", **basis):  # type: ignore[no-untyped-def]
    return {"target_user_id": user_id, "remarks": remarks, "idempotency_key": key(), **basis}


async def test_corrections_need_a_second_administrator_and_stay_within_the_original(tenant):
    _, [fight] = await open_card(tenant)
    pid = (await tenant.create_user("corr", "player"))["id"]
    player = await tenant.login("corr")
    _, entry = await _grant_entry(tenant, pid)
    checker = await second_admin(tenant)

    body = correction(pid, entry_id=entry["id"], amount="40.00")
    proposed = await ok(await tenant.admin.post(f"{BAL}/corrections", json=body), 201)
    assert (proposed["status"], proposed["direction"], D(proposed["amount"])) == ("pending", "debit", D(40))
    assert await admin_balance(tenant, pid) == START + 100  # nothing posts while pending
    assert (await ok(await tenant.admin.get(f"{BAL}/summary")))["pending_corrections"] == 1
    replay = await ok(await tenant.admin.post(f"{BAL}/corrections", json=body))
    assert replay["id"] == proposed["id"] and replay["replayed"] is True

    r = await tenant.admin.post(f"{BAL}/corrections/{proposed['id']}/approve")
    assert r.status_code == 403 and r.json()["error"]["code"] == "second_reviewer_required"
    approved = await ok(await checker.post(f"{BAL}/corrections/{proposed['id']}/approve", json={"remarks": "Verified"}))
    assert (approved["status"], approved["review_remarks"], D(approved["total_delta"])) == ("applied", "Verified", D(-40))
    assert approved["reviewed_by_username"] != approved["created_by_username"]
    assert await balance(player) == START + 60
    again = await ok(await checker.post(f"{BAL}/corrections/{proposed['id']}/approve"))
    assert again["replayed"] is True and await balance(player) == START + 60
    [row] = await items(tenant.admin, "entries", kind="correction", user_id=pid)
    assert (D(row["delta"]), row["operation_reference"], D(row["balance_before"])) == (D(-40), proposed["reference"], START + 100)

    # Only the uncorrected 60 remains; pending proposals count against it, rejected ones do not.
    r = await tenant.admin.post(f"{BAL}/corrections", json=correction(pid, entry_id=entry["id"], amount="70"))
    assert r.status_code == 409 and r.json()["error"]["details"]["remaining"] == "60.00"
    rest = await ok(await tenant.admin.post(f"{BAL}/corrections", json=correction(pid, entry_id=entry["id"])), 201)
    assert D(rest["amount"]) == 60
    assert (
        await tenant.admin.post(f"{BAL}/corrections", json=correction(pid, entry_id=entry["id"], amount="1"))
    ).status_code == 409
    assert (await checker.post(f"{BAL}/corrections/{rest['id']}/reject", json={"remarks": "x"})).status_code == 422
    rejected = await ok(await checker.post(f"{BAL}/corrections/{rest['id']}/reject", json={"remarks": "Not an error"}))
    assert rejected["status"] == "rejected"
    assert (await checker.post(f"{BAL}/corrections/{rest['id']}/approve")).status_code == 409
    await ok(await tenant.admin.post(f"{BAL}/corrections", json=correction(pid, entry_id=entry["id"], amount="60")), 201)

    # Play results are not correctable here (results have their own audited correction flow).
    stake = await placed(player, fight["id"], "meron", 10)
    stake_entry = (await items(tenant.admin, "entries", kind="stake", user_id=pid))[0]
    assert stake_entry["bet_id"] == stake["bet"]["id"]
    r = await tenant.admin.post(f"{BAL}/corrections", json=correction(pid, entry_id=stake_entry["id"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_correctable"
    # Agents have no play balance to correct, and the entry must belong to the named player.
    other = (await tenant.create_user("corr2", "player"))["id"]
    assert (await tenant.admin.post(f"{BAL}/corrections", json=correction(other, entry_id=entry["id"]))).status_code == 404
    agent = (await tenant.create_user("cag", "master_agent"))["id"]
    assert (await tenant.admin.post(f"{BAL}/corrections", json=correction(agent, entry_id=entry["id"]))).status_code == 404

    statuses = sorted(o["status"] for o in await items(tenant.admin, "operations", kind="correction"))
    assert statuses == ["applied", "pending", "rejected"]
    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "balance.correction_approved"})).json()["items"]
    assert len(logs) == 1
    await reconciled(tenant)


async def test_insufficient_balance_keeps_the_correction_pending_and_rolls_back(tenant):
    _, [fight] = await open_card(tenant, starting_balance="100.00")
    pid = (await tenant.create_user("spent", "player"))["id"]
    player = await tenant.login("spent")
    _, entry = await _grant_entry(tenant, pid, 50)
    proposed = await ok(await tenant.admin.post(f"{BAL}/corrections", json=correction(pid, entry_id=entry["id"])), 201)
    await placed(player, fight["id"], "meron", 150)  # the player has since staked everything

    checker = await second_admin(tenant)
    r = await checker.post(f"{BAL}/corrections/{proposed['id']}/approve")
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_balance"
    after = await ok(await tenant.admin.get(f"{BAL}/operations/{proposed['id']}"))
    assert (after["status"], after["reviewed_by_id"], after["accounts_affected"]) == ("pending", None, 0)
    assert await balance(player) == 0
    assert await items(tenant.admin, "entries", kind="correction") == []
    await reconciled(tenant)


async def test_a_missed_uniform_grant_can_be_applied_once(tenant):
    await enable(tenant)
    got = (await tenant.create_user("got", "player"))["id"]
    missed = (await tenant.create_user("missed", "player"))["id"]
    assert (await tenant.admin.post(f"{ADMIN}/users/{missed}/suspend")).status_code == 200
    op = await ok(await grant(tenant.admin, 25), 201)
    assert (await tenant.admin.post(f"{ADMIN}/users/{missed}/activate")).status_code == 200
    late = (await tenant.create_user("late", "player"))["id"]
    checker = await second_admin(tenant)

    r = await tenant.admin.post(f"{BAL}/corrections", json=correction(got, operation_id=op["id"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_received"
    r = await tenant.admin.post(f"{BAL}/corrections", json=correction(late, operation_id=op["id"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_eligible"
    r = await tenant.admin.post(f"{BAL}/corrections", json=correction(missed, operation_id=op["id"], amount="999"))
    assert r.status_code == 422  # a missed grant is always its original amount

    proposed = await ok(await tenant.admin.post(f"{BAL}/corrections", json=correction(missed, operation_id=op["id"])), 201)
    assert (proposed["direction"], D(proposed["amount"]), proposed["corrects_operation_id"]) == ("credit", D(25), op["id"])
    assert (await tenant.admin.post(f"{BAL}/corrections", json=correction(missed, operation_id=op["id"]))).status_code == 409
    await ok(await checker.post(f"{BAL}/corrections/{proposed['id']}/approve"))
    assert await admin_balance(tenant, missed) == START + 25 == await admin_balance(tenant, got)
    assert (await tenant.admin.post(f"{BAL}/corrections", json=correction(missed, operation_id=op["id"]))).status_code == 409
    await reconciled(tenant)


# ---- reset policy ----


async def test_reset_follows_the_policy_and_waits_for_open_stakes(tenant):
    _, [fight] = await open_card(tenant)
    pids = [(await tenant.create_user(n, "player"))["id"] for n in ("rst1", "rst2")]
    r1, r2 = await tenant.login("rst1"), await tenant.login("rst2")
    await ok(await grant(tenant.admin, 500), 201)
    r = await tenant.admin.post(f"{BAL}/resets", json={"idempotency_key": key()})
    assert r.status_code == 409 and r.json()["error"]["code"] == "reset_disabled"
    settings = await ok(await tenant.admin.put(f"{ADMIN}/betting/settings", json={"reset_policy": "starting_balance"}))
    assert settings["reset_policy"] == "starting_balance"

    await placed(r1, fight["id"], "meron", 300)
    await placed(r2, fight["id"], "wala", 200)
    r = await tenant.admin.post(f"{BAL}/resets", json={"idempotency_key": key()})
    assert r.status_code == 409 and r.json()["error"]["details"]["open_stakes"] == 2
    await declare(tenant, fight["id"], "meron")
    assert await balance(r1) != START + 500

    idem = key()
    op = await ok(await tenant.admin.post(f"{BAL}/resets", json={"idempotency_key": idem, "remarks": "New season"}), 201)
    assert (op["kind"], D(op["amount"]), op["accounts_affected"]) == ("reset", START, 2)
    for pid in pids:
        assert await admin_balance(tenant, pid) == START
    replay = await ok(await tenant.admin.post(f"{BAL}/resets", json={"idempotency_key": idem, "remarks": "New season"}))
    assert replay["replayed"] is True
    rows = await items(tenant.admin, "entries", kind="reset")
    assert sum(D(r["delta"]) for r in rows) == D(op["total_delta"])
    assert all(D(r["balance_after"]) == START for r in rows)
    statement = await ok(await r1.get(f"{PORTAL}/betting/statement"))
    total = sum(
        D(statement[k]) for k in ("granted", "account_grants", "uniform_grants", "resets", "balance_corrections", "net_result")
    )
    assert total == D(statement["balance"]) == START
    await reconciled(tenant)


# ---- concurrency ----


async def test_concurrent_requests_with_one_key_post_once(tenant):
    await enable(tenant)
    pids = [(await tenant.create_user(f"con{i}", "player"))["id"] for i in range(3)]
    idem = key()
    results = await asyncio.gather(*(grant(tenant.admin, 10, idem) for _ in range(6)))
    assert sorted(r.status_code for r in results) == [200] * 5 + [201]
    assert len({r.json()["id"] for r in results}) == 1
    for pid in pids:
        assert await admin_balance(tenant, pid) == START + 10
    await reconciled(tenant)


async def test_concurrent_grants_bets_and_corrections_stay_consistent(tenant):
    _, [fight] = await open_card(tenant, starting_balance="1000.00")
    names = [f"cc{i}" for i in range(4)]
    pids = [(await tenant.create_user(n, "player"))["id"] for n in names]
    clients = [await tenant.login(n) for n in names]
    _, entry = await _grant_entry(tenant, pids[0], 100)
    proposed = await ok(await tenant.admin.post(f"{BAL}/corrections", json=correction(pids[0], entry_id=entry["id"])), 201)
    checker = await second_admin(tenant)

    jobs = [grant(tenant.admin, 5) for _ in range(4)]
    jobs += [bet(c, fight["id"], "meron" if i % 2 else "wala", 20) for c in clients for i in range(3)]
    jobs += [checker.post(f"{BAL}/corrections/{proposed['id']}/approve") for _ in range(3)]
    results = await asyncio.gather(*jobs)
    assert all(r.status_code in (200, 201) for r in results), [r.text for r in results if r.status_code >= 300]

    # 1000 start + 100 grant + 4 x 5 uniform grants - 60 staked, less the approved 100 for player 0.
    for i, client in enumerate(clients):
        expected = D(1000) + 100 + 20 - 60 - (100 if i == 0 else 0)
        assert await balance(client) == expected, names[i]
    assert len([r for r in await items(tenant.admin, "entries", kind="correction")]) == 1
    await reconciled(tenant)


# ---- database guards ----


async def test_database_rejects_tampering_with_operations_and_entries(tenant):
    await enable(tenant)
    pid = (await tenant.create_user("guard", "player"))["id"]
    op = await ok(await grant(tenant.admin, 10), 201)
    wallet_id = None
    async with tenant_session(tenant.id) as db:
        wallet_id = (await db.execute(text("SELECT id FROM play_wallets WHERE user_id = :u"), {"u": pid})).scalar_one()
    statements = [
        ("UPDATE balance_operations SET amount = 1 WHERE id = :op", {"op": op["id"]}),
        ("DELETE FROM balance_operations WHERE id = :op", {"op": op["id"]}),
        ("UPDATE play_wallet_entries SET delta = 1 WHERE operation_id = :op", {"op": op["id"]}),
        ("DELETE FROM play_wallet_entries WHERE operation_id = :op", {"op": op["id"]}),
        (
            "INSERT INTO play_wallet_entries (tenant_id, wallet_id, kind, delta, balance_after, idempotency_key)"
            " VALUES (:t, :w, 'uniform_grant', 5, 5, :k)",
            {"t": tenant.id, "w": wallet_id, "k": key()},
        ),
    ]
    for sql, params in statements:
        with pytest.raises(DBAPIError):
            async with tenant_session(tenant.id) as db:
                await db.execute(text(sql), params)
    # An "applied" operation without matching ledger rows cannot be committed.
    with pytest.raises(DBAPIError):
        async with tenant_session(tenant.id) as db:
            await db.execute(
                text(
                    "INSERT INTO balance_operations (tenant_id, reference, kind, status, amount, created_by_id,"
                    " accounts_affected, total_delta, idempotency_key, request_hash)"
                    " VALUES (:t, 'BAL-FORGED', 'uniform_grant', 'applied', 5, :a, 1, 5, :k, 'x')"
                ),
                {"t": tenant.id, "a": tenant.admin_id, "k": key()},
            )
    assert await admin_balance(tenant, pid) == START + 10
    await reconciled(tenant)
