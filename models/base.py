from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from util.time_util import now_kst


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_kst, nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, onupdate=now_kst, nullable=True
    )
