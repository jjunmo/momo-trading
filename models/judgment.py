"""판단 검증 기록 — 에이전트 판단의 예상 vs 실제 결과 채점 데이터"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class JudgmentVerification(Base, TimestampMixin):
    """
    에이전트가 내린 판단의 근거·예상을 구조화 저장하고,
    만기/청산 시 하네스가 실제 결과와 대조해 채점.

    judgment_type:
      - BUY_ANALYSIS: 매수 분석 (목표가/손절가 예상)
      - PEAK_SELL: LLM 정점 판단 매도 (매도 후 하락 예상)
      - MARKET_REGIME: 시장 국면 선언
      - TRADING_RULE: 일일 리뷰 규칙 (expected_effect 검증)

    verdict (채점 전 NULL):
      - TARGET_HIT / STOP_HIT: 매수 판단이 목표/손절에 도달 (완주)
      - INTERVENED_PROFIT / INTERVENED_LOSS: 중간 개입 청산 (수익/손실)
      - CORRECT / WRONG: 정점매도·국면·규칙 판단의 적중 여부
      - EXPIRED: 기한 내 검증 불가 (데이터 부족 등)
    """
    __tablename__ = "judgment_verifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))

    judgment_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # 판단의 원본 레코드 (trade_result.id / trading_rule.id 등)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # 판단 내용
    rationale: Mapped[str] = mapped_column(Text, default="")  # 판단 근거 요약
    expected: Mapped[str] = mapped_column(Text, default="")  # JSON: 방향·목표·기한
    actual: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 실제 결과

    # 채점
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0~1 (부분 점수, 목표 달성률 등)

    judged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 판단 시각
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 채점 시각
