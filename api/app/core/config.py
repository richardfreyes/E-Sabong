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

    invoice_tax_rate_bps: int = 0  # basis points, e.g. 1200 = 12%
    default_currency: str = "PHP"  # ISO 4217; must be in app.core.money.SUPPORTED_CURRENCIES

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

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
            secret = self.jwt_secret.get_secret_value()
            if len(secret) < 32 or secret.startswith("change-me"):
                raise ValueError("JWT_SECRET must be set to a random value of 32+ bytes")
            if self.allow_tenant_header:
                raise ValueError("ALLOW_TENANT_HEADER must be false in production")
        if self.deployment_mode == "dedicated" and not self.dedicated_tenant_slug:
            raise ValueError("DEDICATED_TENANT_SLUG is required in dedicated mode")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
