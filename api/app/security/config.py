"""Edge-guard settings (``SECURITY_*``). Separate from app.core.config so the guard can be tuned or
removed without touching application settings.

Defaults come from the 72-hour production/UAT gateway baseline taken on 2026-11-20
(docs/security/README.md, "Baseline"): the busiest single client address made 76 API reads and
33 writes a minute, a Declarator desk 46 reads and 22 writes, a WebSocket client at most 6
handshakes a minute. Every limit below is at least 15x that, because Philippine mobile carriers
put many players behind one carrier-grade NAT address.
"""

import ipaddress
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GuardMode = Literal["off", "monitor", "enforce"]
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# Routes whose authenticated requests are never refused by the guard (only recorded): betting,
# declarations, balances, wallet moves, commissions, loads. A refused bet or declaration is worse
# than the load it saves; account-level abuse there is handled by the owners, not by a throttle.
DEFAULT_CRITICAL_PREFIXES = (
    "/api/v1/declarator/",
    "/api/v1/portal/matches/",
    "/api/v1/portal/betting",
    "/api/v1/portal/bets",
    "/api/v1/admin/matches/",
    "/api/v1/admin/events/",
    "/api/v1/admin/betting",
    "/api/v1/admin/balance",
    "/api/v1/admin/points",
    "/api/v1/resources/",
    "/api/v1/loader/",
    "/api/v1/commissions/",
    "/api/v1/simulations/",
    "/api/v1/agent/",
)


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECURITY_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # off = pass everything through untouched; monitor = count and log, never refuse;
    # enforce = refuse over-limit requests with 429. None = monitor in production, off elsewhere.
    guard_mode: GuardMode | None = None
    # Shown in every security log line (prod / uat / local). Compose passes ESB_STACK.
    env_name: str | None = None

    # Client addresses (comma-separated IPs/CIDRs) the guard never counts or refuses: monitoring
    # hosts, the office, load-test runners during an approved test. Loopback is always included.
    allowlist: str = ""

    # Per client address, all API requests (flood ceiling), per minute.
    ip_per_minute: int = Field(default=3000, ge=1)
    # Per signed-in account (any route that is not critical), per minute.
    user_per_minute: int = Field(default=1200, ge=1)
    # Per client address, requests without a valid access token, per minute.
    anon_per_minute: int = Field(default=600, ge=1)
    # Per client address, sign-in / refresh / registration / signup requests, per minute. Failed
    # sign-ins keep their own, stricter account and address lockouts (app/modules/auth/service.py).
    auth_per_minute: int = Field(default=120, ge=1)

    # Request bodies (Content-Length). Uploads: branding assets (the handlers allow 2 MiB).
    max_body_bytes: int = Field(default=1024 * 1024, ge=1024)
    max_upload_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)

    # Temporary blocks: an address that breaks the flood ceiling in this many separate minutes
    # within the strike window is refused for block_seconds (never permanent; see the CLI).
    block_after_strikes: int = Field(default=3, ge=1)
    strike_window_seconds: int = Field(default=600, ge=60)
    block_seconds: int = Field(default=600, ge=60, le=86400)

    # WebSockets (/api/ws, /api/ws/simulations).
    ws_handshakes_per_minute_ip: int = Field(default=300, ge=1)
    ws_handshakes_per_minute_user: int = Field(default=60, ge=1)
    ws_max_per_ip: int = Field(default=1000, ge=1)
    ws_max_per_user: int = Field(default=30, ge=1)
    # Client -> server frames per 10 s (the portals send one ping every 25 s).
    ws_messages_per_10s: int = Field(default=60, ge=1)

    # Redis budget per guard call; on timeout or error the request is served unguarded.
    redis_timeout_ms: int = Field(default=100, ge=5, le=2000)

    # Optional per-worker concurrency caps for expensive routes: "/api/v1/admin/reports/=4,...".
    # Empty = no caps. A request that cannot start within concurrency_wait_seconds gets 503.
    concurrency_limits: str = ""
    concurrency_wait_seconds: float = Field(default=2.0, ge=0, le=30)

    # Comma-separated path prefixes treated as critical (see DEFAULT_CRITICAL_PREFIXES). Empty = defaults.
    critical_prefixes: str = ""

    @field_validator("guard_mode", "env_name", mode="before")
    @classmethod
    def _blank_is_default(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def allowlist_networks(self) -> tuple[Network, ...]:
        nets: list[Network] = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
        for item in self.allowlist.replace(" ", ",").split(","):
            if item.strip():
                nets.append(ipaddress.ip_network(item.strip(), strict=False))
        return tuple(nets)

    @property
    def critical(self) -> tuple[str, ...]:
        items = tuple(p.strip() for p in self.critical_prefixes.split(",") if p.strip())
        return items or DEFAULT_CRITICAL_PREFIXES

    @property
    def concurrency(self) -> dict[str, int]:
        caps: dict[str, int] = {}
        for item in self.concurrency_limits.split(","):
            if "=" in item:
                prefix, _, n = item.partition("=")
                if prefix.strip() and n.strip().isdigit() and int(n) > 0:
                    caps[prefix.strip()] = int(n)
        return caps


@lru_cache
def get_security_settings() -> SecuritySettings:
    return SecuritySettings()


def resolved_mode(security: SecuritySettings, *, production: bool) -> GuardMode:
    if security.guard_mode is not None:
        return security.guard_mode
    return "monitor" if production else "off"
