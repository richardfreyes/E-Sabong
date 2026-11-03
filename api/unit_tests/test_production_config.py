"""Production refuses to start with development defaults, placeholders or unsafe switches."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, production_problems

SAFE = {
    "environment": "production",
    "base_domain": "events.ph-operator.net",
    "platform_host": "owner.events.ph-operator.net",
    "public_scheme": "https",
    "cookie_secure": True,
    "jwt_secret": "k" * 48,
    "database_url_app": "postgresql+asyncpg://esabong_app:Zr7q2w9xP4@postgres:5432/esabong",
    "database_url_platform": "postgresql+asyncpg://esabong_platform:Hk3m8t1vB6@postgres:5432/esabong",
    "database_url_migrations": "postgresql+asyncpg://esabong_owner:Qn5c0y4uL2@postgres:5432/esabong",
    "redis_url": "redis://:Wf6j1s8dR3@redis:6379/0",
    "celery_broker_url": "redis://:Wf6j1s8dR3@redis:6379/1",
    "celery_result_backend": "redis://:Wf6j1s8dR3@redis:6379/2",
}


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**SAFE, **overrides})  # type: ignore[arg-type]


def test_safe_production_configuration_starts() -> None:
    s = make()
    assert production_problems(s) == []
    assert s.docs_enabled is False  # docs are opt-in in production


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"jwt_secret": "change-me-in-production-at-least-32-bytes!!"}, "JWT_SECRET"),
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        ({"allow_tenant_header": True}, "ALLOW_TENANT_HEADER"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"public_scheme": "http"}, "PUBLIC_SCHEME"),
        ({"base_domain": "localhost"}, "BASE_DOMAIN"),
        ({"platform_host": "owner.localhost"}, "PLATFORM_HOST"),
        ({"base_domain": "platform.example.com"}, "BASE_DOMAIN"),
        ({"database_url_app": "postgresql+asyncpg://esabong_app:app@localhost:5432/esabong"}, "DATABASE_URL_APP"),
        ({"database_url_migrations": "postgresql+asyncpg://esabong_owner:CHANGE_ME@postgres/esabong"}, "DATABASE_URL_MIGRATIONS"),
        ({"redis_url": "redis://redis:6379/0"}, "REDIS_URL"),
        ({"celery_broker_url": "redis://:@redis:6379/1"}, "CELERY_BROKER_URL"),
        ({"cors_origins": "*"}, "CORS_ORIGINS"),
        ({"video_credentials_key": "too-short"}, "VIDEO_CREDENTIALS_KEY"),
    ],
)
def test_unsafe_production_configuration_is_refused(override: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make(**override)


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(ValidationError) as exc:
        make(cookie_secure=False, public_scheme="http", allow_tenant_header=True)
    text = str(exc.value)
    assert "COOKIE_SECURE" in text and "PUBLIC_SCHEME" in text and "ALLOW_TENANT_HEADER" in text


def test_development_defaults_still_start_outside_production() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.environment == "development" and s.docs_enabled is True
    assert production_problems(s)  # the same values would be refused in production


def test_docs_can_be_enabled_explicitly_in_production() -> None:
    assert make(api_docs_enabled=True).docs_enabled is True


def test_blank_docs_flag_from_compose_means_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_DOCS_ENABLED", "")
    assert make().api_docs_enabled is None and make().docs_enabled is False


def test_blank_optional_secrets_from_compose_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes unset optional variables as empty strings; production must still start."""
    for name in ("VIDEO_CREDENTIALS_KEY", "LICENSE_PRIVATE_KEY_PEM", "LICENSE_KEY", "SMTP_PASSWORD"):
        monkeypatch.setenv(name, "")
    s = make()
    assert s.video_credentials_key is None and s.license_key is None and s.smtp_password is None
    assert production_problems(s) == []


# ---- Contabo layout: root-domain tenant --------------------------------------------------


def test_root_tenant_slug_is_normalised_and_platform_host_may_equal_base_domain() -> None:
    s = make(base_domain="swc.example-operator.net", platform_host="swc.example-operator.net", root_tenant_slug=" Main ")
    assert s.root_tenant_slug == "main"
    assert production_problems(s) == []


@pytest.mark.parametrize("slug", ["a", "bad_slug", "-main", "x" * 64])
def test_invalid_root_tenant_slug_is_refused(slug: str) -> None:
    with pytest.raises(ValidationError, match="ROOT_TENANT_SLUG"):
        make(root_tenant_slug=slug)


def test_blank_root_tenant_slug_from_compose_means_unset() -> None:
    assert make(root_tenant_slug="").root_tenant_slug is None


def test_root_tenant_is_refused_in_dedicated_mode() -> None:
    with pytest.raises(ValidationError, match="shared mode only"):
        make(deployment_mode="dedicated", dedicated_tenant_slug="solo", root_tenant_slug="solo")

