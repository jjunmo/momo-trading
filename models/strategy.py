from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class StrategyConfig(Base, TimestampMixin):
    __tablename__ = "strategy_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)  # STABLE_SHORT, AGGRESSIVE_SHORT
    stop_loss_pct: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit_pct: Mapped[float] = mapped_column(Float, nullable=False)
    max_hold_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_position_pct: Mapped[float] = mapped_column(Float, nullable=False)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.6)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)


class StrategySignal(Base, TimestampMixin):
    __tablename__ = "strategy_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("strategy_configs.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY, SELL, HOLD
    strength: Mapped[float] = mapped_column(Float, default=0.0)  # 0~1
    suggested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency: Mapped[str] = mapped_column(String(10), default="WAIT")  # IMMEDIATE, WAIT
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    stock: Mapped["Stock"] = relationship(back_populates="signals")  # noqa: F821
    strategy: Mapped["StrategyConfig"] = relationship()
