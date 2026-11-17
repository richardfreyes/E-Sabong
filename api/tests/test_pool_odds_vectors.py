"""The web desk projects the MERON/WALA board price with its own copy of the pool formula
(web/shared/services/pool-odds.ts). Both sides check the same cases, so they cannot drift apart."""

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.modules.betting import odds

VECTORS = Path(__file__).resolve().parents[2] / "web" / "shared" / "services" / "pool-odds.vectors.json"


@pytest.mark.skipif(not VECTORS.exists(), reason="web checkout not present (API-only image)")
def test_shared_pool_odds_vectors_match_the_api_formula() -> None:
    cases = json.loads(VECTORS.read_text())["cases"]
    assert len(cases) > 50
    for case in cases:
        price = odds.final_odds(D(case["meron_cents"]) / 100, D(case["wala_cents"]) / 100, D("0.05"))
        assert (str(price.meron), str(price.wala)) == (case["meron"], case["wala"]), case
