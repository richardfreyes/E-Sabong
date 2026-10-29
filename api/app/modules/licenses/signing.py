"""Ed25519-signed license tokens.

Format: ``ESBL1.<base64url(payload json)>.<base64url(signature)>``. The control
plane signs with the private key; dedicated installs verify offline with the
public key, so they keep working without network access until expiry.
"""

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.config import get_settings
from app.core.errors import AppError, Unprocessable

PREFIX = "ESBL1"
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class LicenseError(AppError):
    status_code = 403
    code = "license_invalid"


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _pem(value: str) -> bytes:
    return value.replace("\\n", "\n").encode()


def generate_keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    priv_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub_pem = (
        private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    )
    return priv_pem, pub_pem


def generate_license_key() -> str:
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(5)) for _ in range(5)]
    return "ESB-" + "-".join(groups)


def _private_key() -> Ed25519PrivateKey:
    secret = get_settings().license_private_key_pem
    if secret is None or not secret.get_secret_value().strip():
        raise Unprocessable("License signing key is not configured", code="license_signing_unavailable")
    key = serialization.load_pem_private_key(_pem(secret.get_secret_value()), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise Unprocessable("License signing key must be Ed25519", code="license_signing_unavailable")
    return key


def _public_key(pem: str | None = None) -> Ed25519PublicKey:
    pem = pem or get_settings().license_public_key_pem
    if not pem:
        raise LicenseError("License verification key is not configured")
    key = serialization.load_pem_public_key(_pem(pem))
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("License verification key must be Ed25519")
    return key


def sign(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    signature = _private_key().sign(body)
    return f"{PREFIX}.{_b64e(body)}.{_b64e(signature)}"


@dataclass(frozen=True)
class VerifiedLicense:
    license_id: str
    tenant_slug: str
    licensee: str
    license_type: str
    expires_at: datetime | None
    maintenance_until: datetime | None
    fingerprint: str | None
    raw: dict[str, Any]

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)


def verify(token: str, public_key_pem: str | None = None) -> VerifiedLicense:
    try:
        prefix, body_b64, sig_b64 = token.strip().split(".")
    except ValueError as exc:
        raise LicenseError("Malformed license token") from exc
    if prefix != PREFIX:
        raise LicenseError("Unsupported license token version")
    body = _b64d(body_b64)
    try:
        _public_key(public_key_pem).verify(_b64d(sig_b64), body)
    except InvalidSignature as exc:
        raise LicenseError("License signature is invalid") from exc
    payload = json.loads(body)

    def _dt(v: str | None) -> datetime | None:
        return datetime.fromisoformat(v) if v else None

    return VerifiedLicense(
        license_id=payload["lid"],
        tenant_slug=payload["tenant"],
        licensee=payload["licensee"],
        license_type=payload["type"],
        expires_at=_dt(payload.get("expires_at")),
        maintenance_until=_dt(payload.get("maintenance_until")),
        fingerprint=payload.get("fingerprint"),
        raw=payload,
    )
