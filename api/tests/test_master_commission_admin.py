"""Administrators set each master agent's commission (default 8.5%, 0% to 100%).

Only tenant administrators read or change it on the admin side; a master agent sees their own rate
in /commissions/me. A change applies to bets accepted afterwards: earned entries keep their rate.
"""

from decimal import Decimal as D

from tests.test_betting import ADMIN, declare, open_card, placed

COM = "/api/v1/commissions"


async def rate_of(tenant, master_id):  # type: ignore[no-untyped-def]
    r = await tenant.admin.get(f"{COM}/masters/{master_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def test_each_master_agent_has_its_own_rate_with_an_8_5_default(app, tenant):
    m1 = await tenant.create_user("master1", "master_agent")
    m2 = await tenant.create_user("master2", "master_agent")
    m3 = await tenant.create_user("master3", "master_agent")

    first = await rate_of(tenant, m1["id"])
    assert (first["username"], D(first["allocation"]), first["is_default"]) == ("master1", D("0.085"), True)

    for master, rate in ((m2, "0.0700"), (m3, "0.1000")):
        r = await tenant.admin.put(f"{COM}/masters/{master['id']}", json={"allocation": rate})
        assert r.status_code == 200, r.text
        assert r.json()["allocation"] == rate

    # Stored, independent, and read back the same way after a reload.
    rates = {m["username"]: await rate_of(tenant, m["id"]) for m in (m1, m2, m3)}
    assert {k: D(v["allocation"]) for k, v in rates.items()} == {"master1": D("0.085"), "master2": D("0.07"), "master3": D("0.1")}
    assert (rates["master1"]["is_default"], rates["master2"]["is_default"]) == (True, False)

    # The master agent sees their own rate and cannot change it.
    boss = await tenant.login("master2")
    assert D((await boss.get(f"{COM}/me")).json()["allocation"]) == D("0.07")

    # 0% and 100% are both valid.
    # (Ends at 8.5%: later tests in this session downgrade past 0028, which refuses rates above 50%.)
    for rate in ("0.0000", "1.0000", "0.0850"):
        r = await tenant.admin.put(f"{COM}/masters/{m1['id']}", json={"allocation": rate})
        assert r.status_code == 200 and r.json()["allocation"] == rate, r.text


async def test_invalid_rates_and_non_master_accounts_are_refused(app, tenant):
    master = await tenant.create_user("master1", "master_agent")
    agent = await tenant.create_user("agent1", "agent", parent_id=master["id"])
    for bad in ("-0.0100", "1.0001", "2", "abc", None):
        r = await tenant.admin.put(f"{COM}/masters/{master['id']}", json={"allocation": bad})
        assert r.status_code == 422, (bad, r.text)
    assert D((await rate_of(tenant, master["id"]))["allocation"]) == D("0.085")

    # Only master agents carry this rate: an agent's rate stays with its master agent.
    path = f"{COM}/masters/{agent['id']}"
    assert (await tenant.admin.get(path)).status_code == 404
    assert (await tenant.admin.put(path, json={"allocation": "0.05"})).status_code == 404


async def test_only_tenant_administrators_read_or_change_it(app, tenant, other_tenant):
    master = await tenant.create_user("master1", "master_agent")
    await tenant.create_user("agent1", "agent", parent_id=master["id"])
    await tenant.create_user("ply1", "player")
    await tenant.create_user("judge", "declarator")
    path = f"{COM}/masters/{master['id']}"
    for name in ("master1", "agent1", "ply1", "judge"):
        client = await tenant.login(name)
        assert (await client.put(path, json={"allocation": "0.2"})).status_code == 403, name
        assert (await client.get(path)).status_code == 403, name
    # Another tenant's administrator never resolves the account.
    assert (await other_tenant.admin.get(path)).status_code == 404
    assert (await other_tenant.admin.put(path, json={"allocation": "0.2"})).status_code == 404
    assert D((await rate_of(tenant, master["id"]))["allocation"]) == D("0.085")


async def test_a_change_is_audited_and_applies_only_to_later_bets(app, tenant):
    _, [fight, later] = await open_card(tenant, fights=2)
    master = await tenant.create_user("boss", "master_agent")
    await tenant.create_user("ply1", "player", parent_id=master["id"])
    boss, ply = await tenant.login("boss"), await tenant.login("ply1")

    # A bet at the 8.5% default, settled, then the rate changes.
    await placed(ply, fight["id"], "meron", 1000)
    await declare(tenant, fight["id"], "meron")
    before = (await boss.get(f"{COM}/history")).json()["items"]
    assert [(D(r["master_rate"]), D(r["master_points"])) for r in before] == [(D("0.085"), D("85.00"))]

    r = await tenant.admin.put(f"{COM}/masters/{master['id']}", json={"allocation": "0.1200"})
    assert r.status_code == 200, r.text
    # Saving the same rate again records nothing new.
    assert (await tenant.admin.put(f"{COM}/masters/{master['id']}", json={"allocation": "0.1200"})).status_code == 200

    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "commission.allocation_changed"})).json()["items"]
    assert len(logs) == 1
    log = logs[0]
    assert (log["actor_id"], log["resource_id"]) == (str(tenant.admin_id), master["id"])
    assert log["changes"] == {"allocation": {"from": "0.0850", "to": "0.1200"}}
    assert log["created_at"]

    # The settled entry keeps its 8.5%; the next bet is snapshotted at 12%.
    assert (await tenant.admin.post(f"{ADMIN}/matches/{later['id']}/betting/open")).status_code == 200
    await placed(ply, later["id"], "wala", 500)
    await declare(tenant, later["id"], "wala")
    rows = {r["match_id"]: r for r in (await boss.get(f"{COM}/history", params={"page_size": 50})).json()["items"]}
    assert (D(rows[fight["id"]]["master_rate"]), D(rows[fight["id"]]["master_points"])) == (D("0.085"), D("85.00"))
    assert (D(rows[later["id"]]["master_rate"]), D(rows[later["id"]]["master_points"])) == (D("0.12"), D("60.00"))
    assert D((await boss.get(f"{COM}/me")).json()["total_earned"]) == D("145.00")
