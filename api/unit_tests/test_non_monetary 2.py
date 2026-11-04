"""Guards for the product boundary: play credits and points are never money.

These fail when someone adds a route or a field that would turn play credits or points into
something that can be bought, cashed out or priced in a currency.
"""

import ast
import re
import typing
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from pydantic import BaseModel
from sqlalchemy import CheckConstraint

from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.core.permissions import ALL_PERMISSIONS, SYSTEM_ROLES, P
from app.main import app
from app.modules.auth.models import AccountStatus
from app.modules.betting import schemas as betting_schemas
from app.modules.demo_points import schemas as points_schemas
from app.modules.resources import schemas as resource_schemas
from app.modules.resources import service as resource_service
from app.modules.resources.models import ResourceAction, ResourceTransaction
from app.modules.simulations import schemas as simulation_schemas
from app.modules.users.models import AccountType

CASH_WORDS = re.compile(r"deposit|withdraw|cash-?out|cashout|top-?up|redeem|redemption|purchase|buy-credits|convert|payout", re.I)
# Billing is the one place real money (PHP) exists; it is a platform-owner concern.
BILLING = re.compile(r"/platform/|/billing|/subscription|/invoices|/plans")
ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db/migrations/versions"
RESOURCE_MODULE = ROOT / "api/app/modules/resources"


def routes() -> list[RouteContext]:
    # FastAPI >= 0.16x keeps included routers lazily, so ``app.routes`` lists only a couple of
    # top-level APIRoutes. Iterate effective contexts (full prefixed paths) instead.
    return [c for c in iter_route_contexts(app.routes) if isinstance(c.route, APIRoute)]


def test_route_enumeration_is_not_vacuous() -> None:
    # Every guard below is only as good as this list; it must contain the real, prefixed API.
    found = {(m, r.path) for r in routes() for m in r.methods}
    assert len(found) > 100
    assert ("POST", "/api/v1/auth/login") in found
    assert any(path.startswith("/api/v1/portal/") for _, path in found)
    assert any(path.startswith("/api/v1/admin/points") for _, path in found)


def test_no_route_moves_value_in_or_out_of_play_credits_or_points() -> None:
    offending = [f"{sorted(r.methods)} {r.path}" for r in routes() if CASH_WORDS.search(r.path) and not BILLING.search(r.path)]
    assert offending == []


def test_players_have_no_write_route_on_their_play_credits() -> None:
    # Players stake through POST .../bets only; nothing lets them add to or move a play balance.
    writes = [
        r.path
        for r in routes()
        if r.path.startswith("/api/v1/portal/")
        and {"POST", "PUT", "PATCH"} & r.methods
        and re.search(r"wallet|balance|credit", r.path)
    ]
    assert writes == []


def _models(module: object) -> list[type[BaseModel]]:
    return [v for v in vars(module).values() if isinstance(v, type) and issubclass(v, BaseModel) and v is not BaseModel]


def test_play_credit_and_point_schemas_carry_no_currency() -> None:
    priced = [
        f"{m.__module__}.{m.__name__}.{field}"
        for module in (betting_schemas, points_schemas, simulation_schemas)
        for m in _models(module)
        for field in m.model_fields
        if re.search(r"currency|peso|php|cents", field, re.I)
    ]
    assert priced == []


def test_disclaimer_states_no_cash_value() -> None:
    text = betting_schemas.DISCLAIMER.lower()
    assert "no cash value" in text and "withdrawn" in text and "₱" not in text and "php" not in text


def test_the_retired_points_module_has_no_write_routes_and_players_have_no_points_routes() -> None:
    writes = [
        f"{sorted(r.methods)} {r.path}"
        for r in routes()
        if r.path.startswith("/api/v1/admin/points") and r.methods - {"GET", "HEAD"}
    ]
    assert writes == []
    assert [r.path for r in routes() if r.path.startswith("/api/v1/portal/points")] == []


def test_arena_points_is_the_only_gameplay_balance_wording() -> None:
    assert betting_schemas.DISCLAIMER.startswith("Arena Points")
    assert "credits" not in betting_schemas.DISCLAIMER.lower()


# --- Operational resource ledger (/api/v1/resources) -------------------------------------------
# Resource units must never become a second, transferable play balance: no account may move units
# to another account (agent "loading"), and nothing may convert units into wagerable Arena Points.

RESOURCES = "/api/v1/resources"
TRANSFER_WORDS = re.compile(
    r"allocat|transfer|send|gift|load|share|move|convert|exchange|swap|redeem|payout|points|wager|stake", re.I
)


def _resource_routes() -> list[RouteContext]:
    found = [r for r in routes() if r.path.startswith(RESOURCES)]
    assert found, "resource routes are not registered; this guard would pass vacuously"
    return found


def _body_models(route: RouteContext) -> list[type[BaseModel]]:
    hints = typing.get_type_hints(route.endpoint)
    return [t for name, t in hints.items() if name != "return" and isinstance(t, type) and issubclass(t, BaseModel)]


def test_resource_routes_have_no_transfer_or_conversion_paths() -> None:
    offending = [f"{sorted(r.methods)} {r.path}" for r in _resource_routes() if TRANSFER_WORDS.search(r.path)]
    assert offending == []


def test_resource_write_routes_are_exactly_the_known_single_account_set() -> None:
    # A new write route here is a boundary decision: review it against AGENTS.md before allowing it.
    writes = {(m, r.path) for r in _resource_routes() for m in r.methods - {"GET", "HEAD"}}
    assert writes == {
        ("PUT", RESOURCES + "/settings"),
        ("POST", RESOURCES + "/issuances"),
        ("POST", RESOURCES + "/usage"),
        ("POST", RESOURCES + "/refunds"),
    }


def test_only_admin_issuance_names_a_recipient_and_no_request_names_a_source() -> None:
    with_recipient = []
    for route in _resource_routes():
        for model in _body_models(route):
            fields = set(model.model_fields)
            assert not {f for f in fields if re.search(r"(^|_)(source|from|sender|wallet|tenant)(_|$)", f)}, (route.path, fields)
            if {f for f in fields if re.search(r"(^|_)(target|recipient|to|user)(_|$)", f)}:
                with_recipient.append(route.path)
            assert model.model_config.get("extra") == "forbid", f"{model.__name__} must reject unknown fields"
    assert with_recipient == [RESOURCES + "/issuances"]


def test_resource_issuance_and_recipient_search_require_an_admin_account() -> None:
    tid = uuid.uuid4()
    for tier in (AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.PLAYER):
        current = CurrentUser(
            user=SimpleNamespace(id=uuid.uuid4(), tenant_id=tid, status=AccountStatus.ACTIVE, deleted_at=None, account_type=tier),
            permissions=ALL_PERMISSIONS,
            tenant=SimpleNamespace(id=tid, is_operational=True),
            session_family=uuid.uuid4(),
        )
        with pytest.raises(Forbidden):
            resource_service.targets_query(current)
        with pytest.raises(Forbidden):
            resource_service.authorize(current, P.RESOURCES_ISSUE, admin=True)


def test_resource_actions_and_permissions_have_no_transfer() -> None:
    assert {a.value for a in ResourceAction} == {"issue", "consume", "refund"}
    assert [p.value for p in P if p.value.startswith("resources.") and TRANSFER_WORDS.search(p.value)] == []
    for role in ("master_agent", "agent", "player"):
        assert {p for p in SYSTEM_ROLES[role][1] if p.value.startswith("resources.")} == {P.RESOURCES_CONSUME}


def test_resource_ledger_schema_forbids_account_to_account_movement() -> None:
    checks = {c.name: str(c.sqltext) for c in ResourceTransaction.__table__.constraints if isinstance(c, CheckConstraint)}
    assert checks.get("ck_resource_transactions_no_transfer") == "source_wallet_id IS NULL OR target_wallet_id IS NULL"
    assert "allocate" not in checks["ck_resource_transactions_resource_action_parties"]
    migration = (MIGRATIONS / "20261104_0017_operational_resources.py").read_text()
    assert "ck_resource_transactions_no_transfer CHECK (source_wallet_id IS NULL OR target_wallet_id IS NULL)" in migration
    assert "'allocate'" not in migration and "resources.allocate" not in migration


def test_resource_module_cannot_reach_play_balances_or_currency() -> None:
    forbidden = {"betting", "demo_points", "simulations", "wallets", "billing", "predictions", "matches", "results"}
    for path in RESOURCE_MODULE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            names += [a.name for a in node.names] if isinstance(node, ast.Import) else []
            for name in names:
                assert forbidden.isdisjoint(name.split(".")), f"{path.name} imports {name}"
        source = path.read_text().lower()
        for word in ("play_wallet", "playwallet", "arena_points", "currency", "peso", "php"):
            assert word not in source, f"{path.name} references {word}"
    for model in _models(resource_schemas):
        assert not [f for f in model.model_fields if re.search(r"currency|peso|php|cents|price|points", f, re.I)], model
    assert "no cash value" in resource_schemas.BalanceOut.model_fields["disclaimer"].default.lower()
