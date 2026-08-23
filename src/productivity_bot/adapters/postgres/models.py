from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class TelegramUpdateInboxModel(Base):
    __tablename__ = "telegram_update_inbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed', 'uncertain')",
            name="ck_telegram_update_inbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_update_inbox_attempt_count_non_negative",
        ),
        Index(
            "ix_telegram_update_inbox_ready",
            "status",
            "available_at",
        ),
    )

    update_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default="pending",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
