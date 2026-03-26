"""오버나이트 보유 판정 — 코드 룰 기반 (LLM 폴백용)

LLM Tier1 판정 실패 시 폴백으로 사용된다.
"확실한 위험" 종목만 청산하고 나머지는 보유 유지하는 완화된 기준.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loguru import logger


@dataclass
class HoldDecision:
    action: str  # "HOLD" | "SELL"
    reason: str


def evaluate_overnight_hold(
    holding,
    trade_result,
    current_price: float,
    config,
) -> HoldDecision:
    """종목별 오버나이트 HOLD/SELL 판정

    Args:
        holding: account_manager에서 가져온 보유종목 (symbol, avg_buy_price 등)
        trade_result: TradeResult 모델 (미청산 매수 기록). None이면 SELL.
        current_price: 현재가
        config: Settings 인스턴스

    Returns:
        HoldDecision(action="HOLD"|"SELL", reason="...")
    """
    symbol = holding.symbol

    # TradeResult 없음 → 시스템이 매수하지 않은 종목 (보수적 청산)
    if trade_result is None:
        return HoldDecision("SELL", "TradeResult 없음 — 보수적 청산")

    # 손익률 계산
    avg_price = holding.avg_buy_price
    if avg_price <= 0:
        return HoldDecision("SELL", "매입가 정보 없음")

    pnl_rate = (current_price - avg_price) / avg_price * 100

    # 1. 큰 손실 → SELL (소폭 손실은 스윙에서 정상 변동)
    if pnl_rate < -3.0:
        return HoldDecision(
            "SELL",
            f"손실 과대 ({pnl_rate:+.1f}% < -3%) — 손절 수준 도달",
        )

    # 2. 최대 보유일 초과 → SELL
    hold_days = _calc_hold_days(trade_result)
    max_days = _get_max_hold_days(trade_result.strategy_type, config)
    if hold_days >= max_days:
        return HoldDecision(
            "SELL",
            f"보유 {hold_days}일 ≥ 최대 {max_days}일 — 최대 보유일 초과",
        )

    # 3. AI 신뢰도 매우 낮음 → SELL
    confidence = trade_result.ai_confidence or 0.0
    if confidence < 0.45:
        return HoldDecision(
            "SELL",
            f"AI 신뢰도 {confidence:.2f} < 0.45 — 확신 매우 부족",
        )

    # 4. 목표가 도달 → SELL (익절)
    target_price = trade_result.ai_target_price
    if target_price and target_price > 0 and current_price >= target_price:
        return HoldDecision(
            "SELL",
            f"목표가 도달 (현재 {current_price:,.0f} ≥ 목표 {target_price:,.0f})",
        )

    # 모든 SELL 조건 통과 → HOLD
    target_text = f", 목표 {target_price:,.0f}원" if target_price else ""
    logger.debug(
        "오버나이트 HOLD: {}  수익 {:.1f}%, 신뢰도 {:.2f}, 보유 {}/{}일{}",
        symbol, pnl_rate, confidence, hold_days, max_days, target_text,
    )
    return HoldDecision(
        "HOLD",
        f"수익 {pnl_rate:+.1f}% + 신뢰도 {confidence:.2f} + "
        f"보유 {hold_days}/{max_days}일{target_text}",
    )


def _calc_hold_days(trade_result) -> int:
    """진입일부터 오늘까지 보유일수 계산"""
    from util.time_util import now_kst

    entry_at = trade_result.entry_at or trade_result.created_at
    if not entry_at:
        return 0
    today = now_kst().date()
    entry_date = entry_at.date() if isinstance(entry_at, datetime) else entry_at
    return max(0, (today - entry_date).days)


def _get_max_hold_days(strategy_type: str, config) -> int:
    """전략별 최대 보유일 반환"""
    if "AGGRESSIVE" in (strategy_type or "").upper():
        return config.MAX_HOLD_DAYS_AGGRESSIVE
    return config.MAX_HOLD_DAYS_STABLE
