import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base


class VideoProvider(Base):
    __tablename__ = "video_providers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    credentials: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="disabled")
    checked_at: Mapped[datetime | None]
