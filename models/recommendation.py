from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class Recommendation(Base, TimestampMixin):
    """AI 추천 (반자율 모드용, 승인 대기)"""
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, index=True
    )
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_results.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    suggested_price: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0~1
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True
    )  # PENDING, APPROVED, REJECTED, EXPIRED, EXECUTED
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    stock: Mapped["Stock"] = relationship(back_populates="recommendations")  # noqa: F821
    analysis: Mapped["AnalysisResult"] = relationship(back_populates="recommendations")  # noqa: F821
