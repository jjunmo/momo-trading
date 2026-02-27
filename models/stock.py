from uuid import uuid4

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class Stock(Base, TimestampMixin):
    __tablename__ = "stocks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # KOSPI, NASDAQ 등
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exchange_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    holdings: Mapped[list["PortfolioHolding"]] = relationship(back_populates="stock")  # noqa: F821
    orders: Mapped[list["Order"]] = relationship(back_populates="stock")  # noqa: F821
    daily_data: Mapped[list["MarketDataDaily"]] = relationship(back_populates="stock")  # noqa: F821
    snapshot: Mapped["MarketSnapshot | None"] = relationship(back_populates="stock", uselist=False)  # noqa: F821
    analyses: Mapped[list["AnalysisResult"]] = relationship(back_populates="stock")  # noqa: F821
    signals: Mapped[list["StrategySignal"]] = relationship(back_populates="stock")  # noqa: F821
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="stock")  # noqa: F821
