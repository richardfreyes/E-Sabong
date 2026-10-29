import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
    }


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SoftDelete:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class TenantScoped:
    """Rows owned by one tenant. RLS policies are attached in the migrations."""

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
