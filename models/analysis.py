from uuid import uuid4

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class AnalysisResult(Base, TimestampMixin):
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False)  # TECHNICAL, FUNDAMENTAL, etc.
    recommendation: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY, SELL, HOLD
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0~1
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_provider: Mapped[str] = mapped_column(String(20), nullable=False)  # GOOGLE, CLAUDE, BEDROCK
    llm_tier: Mapped[str] = mapped_column(String(10), nullable=False)  # TIER1, TIER2, FALLBACK
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    stock: Mapped["Stock"] = relationship(back_populates="analyses")  # noqa: F821
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="analysis")  # noqa: F821
