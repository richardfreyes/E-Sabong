"""Request classification for the edge guard and the security logs.

Classes: exempt (health checks, container-internal callbacks), auth, public, upload, critical,
general, websocket. HLS and WebRTC media never reach the API (Caddy sends /stream/* straight to
MediaMTX), so no API limit can touch live video.
"""

EXEMPT_PATHS = ("/api/health", "/api/ready")
# Container-to-container only (the edge answers 404): MediaMTX asks /internal/mediamtx/auth before
# every viewer session, Caddy asks /internal/tls-ask. Never throttled.
EXEMPT_PREFIXES = ("/api/v1/internal/",)
AUTH_PATHS = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/register",
    "/api/v1/platform/auth/login",
    "/api/v1/platform/auth/refresh",
    "/api/v1/public/signup-requests",
    "/api/v1/licensing/activate",
    "/api/v1/licensing/heartbeat",
)
PUBLIC_PREFIX = "/api/v1/public/"
UPLOAD_MARKER = "/branding/assets/"
WEBSOCKET_PREFIX = "/api/ws"


def classify(path: str, critical_prefixes: tuple[str, ...]) -> str:
    if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
        return "exempt"
    if path.startswith(WEBSOCKET_PREFIX):
        return "websocket"
    if path.startswith(AUTH_PATHS):
        return "auth"
    if UPLOAD_MARKER in path:
        return "upload"
    if path.startswith(PUBLIC_PREFIX):
        return "public"
    if path.startswith(critical_prefixes):
        return "critical"
    return "general"
