"""트레이딩 규칙 — 일일 리뷰 피드백에서 도출된 코드 레벨 하드 강제 규칙"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class TradingRule(Base, TimestampMixin):
    """
    AI 일일 리뷰에서 도출된 매매 규칙.
    코드 레벨에서 하드 강제 (프롬프트 제안이 아님).
    자동 만료(expires_at)로 무한 누적 방지.

    rule_type:
      - PARAM_OVERRIDE: 전략 파라미터 오버라이드 (min_confidence, stop_loss_pct 등)
      - VALIDATION_TOGGLE: 검증 로직 활성화 (revalidate_rr_ratio, require_stop_loss_logging)

    strategy_type:
      - ALL: 모든 전략에 적용
      - STABLE_SHORT / AGGRESSIVE_SHORT: 특정 전략에만 적용
    """
    __tablename__ = "trading_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))

    # 규칙 분류
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    strategy_type: Mapped[str] = mapped_column(String(30), default="ALL")

    # 파라미터
    param_name: Mapped[str] = mapped_column(String(50), nullable=False)
    param_value: Mapped[float] = mapped_column(Float, nullable=False)

    # 출처 + 근거
    source: Mapped[str] = mapped_column(String(30), default="DAILY_REVIEW")
    reason: Mapped[str] = mapped_column(Text, default="")
    source_report_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="MEDIUM")

    # 수명
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # 성과 추적
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
