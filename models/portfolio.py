from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)  # STABLE_SHORT / AGGRESSIVE_SHORT
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")  # PAPER / LIVE
    budget: Mapped[float] = mapped_column(Float, default=0.0)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    holdings: Mapped[list["PortfolioHolding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioHolding(Base, TimestampMixin):
    __tablename__ = "portfolio_holdings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.id"), nullable=False, index=True
    )
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    avg_buy_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # Relationships
    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")
    stock: Mapped["Stock"] = relationship(back_populates="holdings")  # noqa: F821
