"""일일 리포트 - 장 마감 후 성과+학습+플랜 요약"""
from datetime import date

from uuid import uuid4

from sqlalchemy import Date, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class DailyReport(Base, TimestampMixin):
    __tablename__ = "daily_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)

    # 오늘 성과
    total_cycles: Mapped[int] = mapped_column(Integer, default=0)
    total_analyses: Mapped[int] = mapped_column(Integer, default=0)
    total_recommendations: Mapped[int] = mapped_column(Integer, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)

    # AI 요약 (LLM 생성)
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    performance_review: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_learned: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_day_plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 상세 데이터 (JSON)
    top_picks: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_stats: Mapped[str | None] = mapped_column(Text, nullable=True)
