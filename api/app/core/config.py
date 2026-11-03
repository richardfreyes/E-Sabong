from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.money import SUPPORTED_CURRENCIES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "Event Platform"
    log_level: str = "INFO"
    log_json: bool = True

    # shared = multi-tenant SaaS; dedicated = licensed single-tenant install
    deployment_mode: Literal["shared", "dedicated"] = "shared"
    dedicated_tenant_slug: str | None = None

    base_domain: str = "localhost"
    platform_host: str = "owner.localhost"
    allow_tenant_header: bool = False
    public_scheme: Literal["http", "https"] = "https"

    # Three database identities: migrations own the schema, the tenant engine is
    # subject to RLS, and the platform engine bypasses it for owner operations.
    database_url_app: str = "postgresql+asyncpg://esabong_app:app@localhost:5432/esabong"
    database_url_platform: str = "postgresql+asyncpg://esabong_platform:platform@localhost:5432/esabong"
    database_url_migrations: str = "postgresql+asyncpg://esabong_owner:owner@localhost:5432/esabong"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_app_role: str = "esabong_app"
    db_platform_role: str = "esabong_platform"

    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = Field(default=100, ge=1, le=4096)
    redis_pubsub_max_connections: int = Field(default=1024, ge=1, le=4096)
    redis_pool_timeout_seconds: float = Field(default=5, gt=0, le=30)
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    jwt_secret: SecretStr = SecretStr("change-me-in-production-at-least-32-bytes!!")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 14 * 24 * 3600
    cookie_secure: bool = True

    login_max_attempts: int = 5
    login_lockout_seconds: int = 15 * 60

    # Ed25519 keys (PEM). The private key lives only on the SaaS control plane;
    # dedicated installs ship with the public key to verify licenses offline.
    license_private_key_pem: SecretStr | None = None
    license_public_key_pem: str | None = None
    license_token: str | None = None
    license_key: SecretStr | None = None  # dedicated installs: used for activation/heartbeat
    license_heartbeat_url: str | None = None
    instance_fingerprint: str | None = None

    video_credentials_key: SecretStr | None = None
    video_api_hosts: list[str] = Field(default_factory=list)
    video_media_hosts: list[str] = Field(default_factory=list)

    # Live streaming through the MediaMTX container. Streaming stays off until the control-API
    # password is set (it is sent as an Authorization header, never in a URL). MediaMTX's auth
    # callback carries no secret: it is reachable only container-to-container (see
    # app/modules/video/stream_router.py) and answers only allow/deny.
    mediamtx_api_url: str = "http://mediamtx:9997"
    mediamtx_api_user: str = "esabong-api"
    mediamtx_api_password: SecretStr | None = None
    # Shown to operators for OBS. Example: rtmp://stream.example.com:1935/live
    stream_rtmp_url: str = ""
    stream_rtmps_url: str = ""
    # Browser ICE servers. TURN credentials are minted per playback from the shared secret
    # (coturn use-auth-secret), so no long-lived TURN password reaches players.
    stream_stun_urls: list[str] = Field(default_factory=list)
    stream_turn_urls: list[str] = Field(default_factory=list)
    stream_turn_secret: SecretStr | None = None
    # Exact hostnames MediaMTX may pull external sources from (server-side fetches).
    video_source_hosts: list[str] = Field(default_factory=list)
    # Synthetic test-pattern streams. None = available outside production.
    stream_test_source: bool | None = None
    stream_max_per_tenant: int = Field(default=10, ge=1, le=100)

    media_root: str = "/data/media"
    media_url_prefix: str = "/api/media"
    max_upload_bytes: int = 2 * 1024 * 1024

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = False
    mail_from: str = "no-reply@localhost"

    cors_origins: list[str] = Field(default_factory=list)

    # Interactive API docs (/api/docs, /api/redoc, /api/openapi.json). None = on outside production.
    api_docs_enabled: bool | None = None

    # Per-client-IP throttles that complement the per-account lockout above: they stop one
    # address from spraying passwords across many accounts or mass-creating accounts.
    login_ip_max_failures: int = 50
    login_ip_window_seconds: int = 15 * 60
    register_ip_max_per_hour: int = 10

    invoice_tax_rate_bps: int = 0  # basis points, e.g. 1200 = 12%
    default_currency: str = "PHP"  # ISO 4217; must be in app.core.money.SUPPORTED_CURRENCIES

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator(
        "api_docs_enabled",
        "video_credentials_key",
        "license_private_key_pem",
        "license_key",
        "smtp_password",
        "mediamtx_api_password",
        "stream_turn_secret",
        "stream_test_source",
        mode="before",
    )
    @classmethod
    def _blank_is_default(cls, v: object) -> object:
        # docker-compose passes unset optional variables as "" (e.g. ${VIDEO_CREDENTIALS_KEY:-}).
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("default_currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"DEFAULT_CURRENCY must be one of: {', '.join(SUPPORTED_CURRENCIES)}")
        return v

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.environment == "production":
            problems = production_problems(self)
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        if self.deployment_mode == "dedicated" and not self.dedicated_tenant_slug:
            raise ValueError("DEDICATED_TENANT_SLUG is required in dedicated mode")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        return (not self.is_production) if self.api_docs_enabled is None else self.api_docs_enabled

    @property
    def streaming_configured(self) -> bool:
        return self.mediamtx_api_password is not None

    @property
    def test_source_available(self) -> bool:
        return (not self.is_production) if self.stream_test_source is None else self.stream_test_source


# Credentials baked into the defaults above and the development compose file. A production
# process must never start with them.
_DEFAULT_DB_PASSWORDS = (":app@", ":platform@", ":owner@")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "example.com")


def _redis_has_password(url: str) -> bool:
    # redis://:password@host or redis://user:password@host
    netloc = url.split("://", 1)[-1].split("/", 1)[0]
    if "@" not in netloc:
        return False
    userinfo = netloc.rsplit("@", 1)[0]
    return bool(userinfo.split(":", 1)[-1])


def production_problems(s: "Settings") -> list[str]:
    """Every reason this configuration is unsafe to run as production; empty when it is safe."""
    problems: list[str] = []
    secret = s.jwt_secret.get_secret_value()
    if len(secret) < 32 or secret.lower().startswith("change"):
        problems.append("JWT_SECRET must be a random value of 32+ bytes")
    if s.allow_tenant_header:
        problems.append("ALLOW_TENANT_HEADER must be false")
    if not s.cookie_secure:
        problems.append("COOKIE_SECURE must be true")
    if s.public_scheme != "https":
        problems.append("PUBLIC_SCHEME must be https")
    for name in ("base_domain", "platform_host"):
        value = getattr(s, name).lower()
        if any(value == h or value.endswith("." + h) for h in _LOCAL_HOSTS):
            problems.append(f"{name.upper()} must be a real public domain, not {value}")
    for name in ("database_url_app", "database_url_platform", "database_url_migrations"):
        url = getattr(s, name)
        if any(marker in url for marker in _DEFAULT_DB_PASSWORDS) or "CHANGE_ME" in url:
            problems.append(f"{name.upper()} uses a default or placeholder password")
    for name in ("redis_url", "celery_broker_url", "celery_result_backend"):
        url = getattr(s, name)
        if not _redis_has_password(url) or "CHANGE_ME" in url:
            problems.append(f"{name.upper()} must authenticate with a password")
    if "*" in s.cors_origins:
        problems.append("CORS_ORIGINS must not contain '*'")
    if s.video_credentials_key is not None and len(s.video_credentials_key.get_secret_value()) < 32:
        problems.append("VIDEO_CREDENTIALS_KEY must be a Fernet key")
    for name, minimum in (("mediamtx_api_password", 24), ("stream_turn_secret", 32)):
        value = getattr(s, name)
        if value is not None and (len(value.get_secret_value()) < minimum or "CHANGE_ME" in value.get_secret_value()):
            problems.append(f"{name.upper()} must be a random value of {minimum}+ characters")
    if s.stream_test_source:
        problems.append("STREAM_TEST_SOURCE must be off in production")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
