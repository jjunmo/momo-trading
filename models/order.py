from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.id"), nullable=False, index=True
    )
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, index=True
    )
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)  # MARKET / LIMIT
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="AI")  # AI / MANUAL / RISK

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 시장가 주문 시 None
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    filled_price: Mapped[float] = mapped_column(Float, default=0.0)

    kis_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    stock: Mapped["Stock"] = relationship(back_populates="orders")  # noqa: F821
