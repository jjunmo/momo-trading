"""에이전트 활동 로그 - 채팅형 모니터링을 위한 모든 활동 기록"""
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class AgentActivityLog(Base, TimestampMixin):
    __tablename__ = "agent_activity_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    activity_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # CYCLE / SCAN / SCREENING / TIER1_ANALYSIS / TIER2_REVIEW /
    # STRATEGY_EVAL / RISK_CHECK / DECISION / EVENT / REPORT

    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    # START / PROGRESS / COMPLETE / ERROR

    stock_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("stocks.id"), nullable=True
    )
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)

    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    llm_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    llm_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
