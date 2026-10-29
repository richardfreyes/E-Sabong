import uuid
from datetime import datetime

from pydantic import Field, model_validator

from app.modules.licenses.models import DeploymentStatus, LicenseStatus, LicenseType
from app.shared.schemas import Schema


class LicenseOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    key_prefix: str
    license_type: LicenseType
    status: LicenseStatus
    licensee_name: str
    max_activations: int
    seats: int | None
    expires_at: datetime | None
    maintenance_until: datetime | None
    agreement_reference: str | None
    notes: str | None
    revoked_at: datetime | None
    revoked_reason: str | None
    created_at: datetime
    activation_count: int = 0


class LicenseCreate(Schema):
    tenant_id: uuid.UUID
    license_type: LicenseType
    licensee_name: str = Field(min_length=2, max_length=200)
    max_activations: int = Field(default=1, ge=1, le=100)
    seats: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    maintenance_until: datetime | None = None
    agreement_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _term(self) -> "LicenseCreate":
        if self.license_type == LicenseType.TERM and self.expires_at is None:
            raise ValueError("Term licenses require expires_at")
        if self.license_type == LicenseType.PERPETUAL and self.expires_at is not None:
            raise ValueError("Perpetual licenses cannot expire; use maintenance_until for support")
        return self


class LicenseIssued(Schema):
    license: LicenseOut
    license_key: str = Field(description="Shown once. Store it securely; only a hash is kept.")


class LicenseUpdate(Schema):
    maintenance_until: datetime | None = None
    expires_at: datetime | None = None
    max_activations: int | None = Field(default=None, ge=1, le=100)
    notes: str | None = Field(default=None, max_length=5000)


class RevokeLicense(Schema):
    reason: str = Field(min_length=3, max_length=300)


class ActivationOut(Schema):
    id: uuid.UUID
    instance_fingerprint: str
    hostname: str | None
    app_version: str | None
    activated_at: datetime
    last_seen_at: datetime | None
    deactivated_at: datetime | None


class ActivateRequest(Schema):
    license_key: str = Field(min_length=10, max_length=64)
    instance_fingerprint: str = Field(min_length=8, max_length=128)
    hostname: str | None = Field(default=None, max_length=253)
    app_version: str | None = Field(default=None, max_length=32)


class HeartbeatRequest(ActivateRequest):
    pass


class LicenseTokenResponse(Schema):
    status: LicenseStatus
    license_token: str | None
    expires_at: datetime | None
    maintenance_until: datetime | None


class DeploymentOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    name: str
    hostname: str
    region: str | None
    status: DeploymentStatus
    license_id: uuid.UUID | None
    app_version: str | None
    last_heartbeat_at: datetime | None
    notes: str | None
    created_at: datetime


class DeploymentCreate(Schema):
    tenant_id: uuid.UUID
    name: str = Field(min_length=2, max_length=120)
    hostname: str = Field(min_length=4, max_length=253)
    region: str | None = Field(default=None, max_length=64)
    license_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=5000)


class DeploymentUpdate(Schema):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    region: str | None = Field(default=None, max_length=64)
    status: DeploymentStatus | None = None
    license_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=5000)


class LicenseState(Schema):
    mode: str
    valid: bool
    reason: str | None
    licensee: str | None = None
    expires_at: datetime | None = None
    maintenance_until: datetime | None = None
