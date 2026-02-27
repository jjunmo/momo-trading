from datetime import date
from uuid import uuid4

from sqlalchemy import Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class MarketDataDaily(Base, TimestampMixin):
    __tablename__ = "market_data_daily"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_stock_trade_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, index=True
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    stock: Mapped["Stock"] = relationship(back_populates="daily_data")  # noqa: F821


class MarketSnapshot(Base, TimestampMixin):
    __tablename__ = "market_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, unique=True, index=True
    )
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    change: Mapped[float] = mapped_column(Float, default=0.0)
    change_rate: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    open: Mapped[float] = mapped_column(Float, default=0.0)
    per: Mapped[float | None] = mapped_column(Float, nullable=True)
    pbr: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    stock: Mapped["Stock"] = relationship(back_populates="snapshot")  # noqa: F821
