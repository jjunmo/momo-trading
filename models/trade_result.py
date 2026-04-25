"""매매 결과 기록 - AI 피드백 루프의 핵심 데이터"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class TradeResult(Base, TimestampMixin):
    """
    실제 매매 결과 기록.
    AI 분석 → 매매 → 결과까지의 전체 이력을 추적하여
    전략별/종목별/패턴별 승률 분석과 AI 프롬프트 개선에 활용.
    """
    __tablename__ = "trade_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True, index=True)
    stock_symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # 매매 정보
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    strategy_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, default=0.0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # 결과 — 수수료/세금 차감 후 순손익 (2026-04-25부터)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)  # 순손익 (원, 수수료·세금 차감)
    return_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 순수익률 (%)
    is_win: Mapped[bool] = mapped_column(Boolean, default=False)
    commission_amt: Mapped[float] = mapped_column(Float, default=0.0)  # 매수+매도 수수료 합
    tax_amt: Mapped[float] = mapped_column(Float, default=0.0)  # 매도 거래세
    hold_days: Mapped[int] = mapped_column(Integer, default=0)
    exit_reason: Mapped[str] = mapped_column(String(30), default="")  # SIGNAL, STOP_LOSS, TAKE_PROFIT, MAX_HOLD

    # AI 분석 당시 컨텍스트 (피드백용)
    ai_recommendation: Mapped[str] = mapped_column(String(10), default="")  # BUY/SELL/HOLD
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ai_target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 진입 시 기술 지표 (패턴 학습용)
    entry_rsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_macd_hist: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_bb_position: Mapped[str | None] = mapped_column(String(20), nullable=True)  # UPPER/MIDDLE/LOWER
    entry_pattern: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 차트 패턴

    # 시장 상태
    market: Mapped[str] = mapped_column(String(10), default="KRX")
    market_regime: Mapped[str] = mapped_column(String(20), default="")  # BULLISH/BEARISH/SIDEWAYS

    # 메모
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 체결 확인 상태: PENDING_CONFIRM → CONFIRMED / CONFIRM_FAILED
    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED", index=True)

    entry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 다음 재평가 시각 (LLM이 결정, 종목별 동적 주기)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
