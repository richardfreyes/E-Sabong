"""Pure unit tests (no database)."""

import uuid

import pytest

from app.core.errors import Unauthorized, Unprocessable
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_temporary_password,
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.modules.demo_points.service import request_hash
from app.modules.reports.service import to_csv
from app.modules.tenants.service import normalize_domain, validate_slug


def test_password_hashing_round_trip() -> None:
    h = hash_password("Correct-Horse-9")
    assert h.startswith("$argon2id$")
    assert verify_password("Correct-Horse-9", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", None)


@pytest.mark.parametrize("pw", ["short1!A", "alllowercaseletters", "ALLUPPER12345", "a" * 129])
def test_password_policy_rejects_weak(pw: str) -> None:
    with pytest.raises(Unprocessable):
        validate_password_policy(pw)


def test_temporary_passwords_satisfy_policy() -> None:
    for _ in range(50):
        validate_password_policy(generate_temporary_password())


def test_access_token_round_trip_and_audience() -> None:
    sub, tid, fam = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token, ttl = create_access_token(subject_id=sub, audience="tenant", tenant_id=tid, family=fam)
    claims = decode_access_token(token, "tenant")
    assert (claims.subject_id, claims.tenant_id, claims.session_family) == (sub, tid, fam) and ttl == 900
    with pytest.raises(Unauthorized):
        decode_access_token(token, "platform")
    with pytest.raises(Unauthorized):
        decode_access_token(token[:-2] + "xx", "tenant")


def test_request_hash_is_order_independent_and_sensitive() -> None:
    a = request_hash(op="transfer", user="u", amount=5, memo=None)
    assert a == request_hash(memo=None, amount=5, user="u", op="transfer")
    assert a != request_hash(op="transfer", user="u", amount=6, memo=None)


def test_csv_neutralises_formula_injection() -> None:
    out = to_csv([{"username": '=HYPERLINK("x")', "balance": 5}])
    assert "'=HYPERLINK" in out


@pytest.mark.parametrize("slug", ["ab", "-bad", "bad-", "Has Space", "www", "under_score"])
def test_invalid_slugs(slug: str) -> None:
    with pytest.raises(Unprocessable):
        validate_slug(slug)


def test_domain_normalisation() -> None:
    assert normalize_domain("HTTPS://Play.Example.com/login") == "play.example.com"
    for bad in ("localhost", "x.test.local", "not a domain", "owner.test.local"):
        with pytest.raises(Unprocessable):
            normalize_domain(bad)


def test_money_rounds_half_up_exactly() -> None:
    from decimal import Decimal

    from app.core.money import basis_points, scale

    assert basis_points(500_000, 1200) == 60_000  # 12% VAT on ₱5,000.00
    assert basis_points(5, 1000) == 1  # 0.5 centavo rounds up, not to even
    assert scale(10_000, Decimal(1) / Decimal(3)) == 3_333
    assert scale(2_995, Decimal("0.5")) == 1_498
    assert scale(4_900, Decimal(0)) == 0


def test_default_currency_must_be_supported() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    assert Settings(default_currency="php").default_currency == "PHP"
    with pytest.raises(ValidationError):
        Settings(default_currency="USD")
