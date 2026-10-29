import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class LicenseType(StrEnum):
    PERPETUAL = "perpetual"
    TERM = "term"


class LicenseStatus(StrEnum):
    ISSUED = "issued"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class DeploymentStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DECOMMISSIONED = "decommissioned"


class LicenseKey(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "license_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    license_type: Mapped[LicenseType] = mapped_column(str_enum(LicenseType), nullable=False)
    status: Mapped[LicenseStatus] = mapped_column(str_enum(LicenseStatus), nullable=False, default=LicenseStatus.ISSUED)
    licensee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    max_activations: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    seats: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None]
    maintenance_until: Mapped[datetime | None]
    agreement_reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("platform_users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(300))


class LicenseActivation(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "license_activations"
    __table_args__ = (UniqueConstraint("license_id", "instance_fingerprint", name="uq_license_activation"),)

    license_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("license_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    instance_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(253))
    app_version: Mapped[str | None] = mapped_column(String(32))
    activated_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime | None]
    deactivated_at: Mapped[datetime | None]


class DeploymentInstance(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "deployment_instances"
    __table_args__ = (Index("ix_deployment_instances_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    region: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[DeploymentStatus] = mapped_column(
        str_enum(DeploymentStatus), nullable=False, default=DeploymentStatus.PROVISIONING
    )
    license_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("license_keys.id", ondelete="SET NULL"))
    app_version: Mapped[str | None] = mapped_column(String(32))
    last_heartbeat_at: Mapped[datetime | None]
    notes: Mapped[str | None] = mapped_column(Text)
