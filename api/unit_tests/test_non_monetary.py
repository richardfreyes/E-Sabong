"""Guards for the product boundary: play credits and points are never money.

These fail when someone adds a route or a field that would turn play credits or points into
something that can be bought, cashed out or priced in a currency.
"""

import re

from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.main import app
from app.modules.betting import schemas as betting_schemas
from app.modules.demo_points import schemas as points_schemas
from app.modules.simulations import schemas as simulation_schemas

CASH_WORDS = re.compile(r"deposit|withdraw|cash-?out|cashout|top-?up|redeem|redemption|purchase|buy-credits|convert|payout", re.I)
# Billing is the one place real money (PHP) exists; it is a platform-owner concern.
BILLING = re.compile(r"/platform/|/billing|/subscription|/invoices|/plans")


def routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


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
