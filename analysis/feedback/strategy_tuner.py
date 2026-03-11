"""승률 기반 전략 파라미터 자동 조정"""
from loguru import logger
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.feedback.performance_tracker import PerformanceTracker
from models.trade_result import TradeResult


class StrategyTuner:
    """
    과거 매매 결과를 분석하여 전략 파라미터 조정을 제안.

    자동 조정 항목:
    - 손절/익절 비율: 실제 청산 패턴 분석
    - 최소 신뢰도: 신뢰도 구간별 승률 분석
    - 최대 보유 기간: 보유 기간별 수익률 분석
    """

    MIN_TRADES_FOR_TUNING = 20  # 최소 거래 수

    def __init__(self, session: AsyncSession):
        self.session = session
        self.tracker = PerformanceTracker(session)

    async def suggest_adjustments(self, strategy_type: str) -> dict:
        """전략 파라미터 조정 제안"""
        stmt = (
            select(TradeResult)
            .where(TradeResult.strategy_type == strategy_type)
            .order_by(TradeResult.created_at.desc())
            .limit(200)
        )
        result = await self.session.execute(stmt)
        trades = list(result.scalars().all())

        if len(trades) < self.MIN_TRADES_FOR_TUNING:
            return {
                "status": "insufficient_data",
                "message": f"조정을 위해 최소 {self.MIN_TRADES_FOR_TUNING}거래 필요 (현재 {len(trades)})",
                "adjustments": [],
            }

        adjustments = []

        # 1. 손절 분석
        sl_adj = self._analyze_stop_loss(trades)
        if sl_adj:
            adjustments.append(sl_adj)

        # 2. 익절 분석
        tp_adj = self._analyze_take_profit(trades)
        if tp_adj:
            adjustments.append(tp_adj)

        # 3. 보유 기간 분석
        hold_adj = self._analyze_hold_period(trades)
        if hold_adj:
            adjustments.append(hold_adj)

        # 4. 신뢰도 임계값 분석
        conf_adj = self._analyze_confidence(trades)
        if conf_adj:
            adjustments.append(conf_adj)

        stat = self.tracker._calc_stat(trades)

        return {
            "status": "ok",
            "strategy_type": strategy_type,
            "total_trades": len(trades),
            "current_win_rate": round(stat.win_rate * 100, 1),
            "current_avg_return": round(stat.avg_return, 2),
            "adjustments": adjustments,
        }

    def _analyze_stop_loss(self, trades: list[TradeResult]) -> dict | None:
        """손절 패턴 분석"""
        sl_trades = [t for t in trades if t.exit_reason == "STOP_LOSS"]
        if len(sl_trades) < 3:
            return None

        sl_returns = [t.return_pct for t in sl_trades]
        avg_sl = sum(sl_returns) / len(sl_returns)
        sl_ratio = len(sl_trades) / len(trades)

        if sl_ratio > 0.4:
            # 평균 손절 수익률에서 20% 여유를 둔 값 제안
            suggested = round(avg_sl * 1.2, 1)
            return {
                "param": "stop_loss_pct",
                "issue": f"손절 비율 과다 ({sl_ratio * 100:.0f}%)",
                "suggestion": "손절 폭을 넓히거나 진입 조건을 강화하세요",
                "suggested_value": max(-8.0, min(-1.0, suggested)),
                "avg_sl_return": round(avg_sl, 2),
                "sl_count": len(sl_trades),
            }

        # 손절가에 근접했다가 반등한 경우가 많으면 손절 폭 확대 제안
        near_miss = [t for t in trades if t.is_win and t.return_pct < 2.0 and t.hold_days >= 3]
        if len(near_miss) > len(trades) * 0.2:
            return {
                "param": "stop_loss_pct",
                "issue": "간신히 수익 전환된 거래가 많음 (손절 폭 적절)",
                "suggestion": "현재 손절 폭 유지 권장",
                "near_miss_count": len(near_miss),
            }

        return None

    def _analyze_take_profit(self, trades: list[TradeResult]) -> dict | None:
        """익절 패턴 분석"""
        tp_trades = [t for t in trades if t.exit_reason == "TAKE_PROFIT"]
        if len(tp_trades) < 3:
            return None

        tp_returns = [t.return_pct for t in tp_trades]
        avg_tp = sum(tp_returns) / len(tp_returns)

        # 익절 후 더 올랐을 수 있는 경우 (익절이 너무 빠름)
        # → 정확한 판단은 불가능하지만, 대부분 익절가에서 정확히 청산된다면 익절 확대 고려
        tp_ratio = len(tp_trades) / len(trades)
        if tp_ratio > 0.3 and avg_tp < 3.0:
            suggested = round(avg_tp * 1.5, 1)
            return {
                "param": "take_profit_pct",
                "issue": f"익절 비율 {tp_ratio * 100:.0f}%, 평균 익절 수익 {avg_tp:.1f}%",
                "suggestion": "익절 폭을 약간 확대하여 수익 극대화 고려",
                "suggested_value": max(2.0, min(15.0, suggested)),
                "tp_count": len(tp_trades),
                "avg_tp_return": round(avg_tp, 2),
            }

        return None

    def _analyze_hold_period(self, trades: list[TradeResult]) -> dict | None:
        """보유 기간 분석"""
        max_hold_exits = [t for t in trades if t.exit_reason == "MAX_HOLD_DAYS"]
        if len(max_hold_exits) < 3:
            return None

        # 최대 보유 기간 만료 청산의 수익률 분석
        mh_returns = [t.return_pct for t in max_hold_exits]
        avg_mh_return = sum(mh_returns) / len(mh_returns)
        mh_win_rate = sum(1 for r in mh_returns if r > 0) / len(mh_returns)

        if avg_mh_return < 0 and mh_win_rate < 0.4:
            return {
                "param": "max_hold_days",
                "issue": f"보유 기간 만료 청산 승률 {mh_win_rate * 100:.0f}%, 평균 수익 {avg_mh_return:+.2f}%",
                "suggestion": "최대 보유 기간을 줄여 손실 축소 고려",
                "max_hold_count": len(max_hold_exits),
            }
        elif avg_mh_return > 1.0 and mh_win_rate > 0.6:
            return {
                "param": "max_hold_days",
                "issue": f"보유 기간 만료 시에도 수익 발생 (승률 {mh_win_rate * 100:.0f}%)",
                "suggestion": "최대 보유 기간을 늘려 수익 극대화 고려",
                "max_hold_count": len(max_hold_exits),
            }

        return None

    def _analyze_confidence(self, trades: list[TradeResult]) -> dict | None:
        """AI 신뢰도 임계값 분석"""
        trades_with_conf = [t for t in trades if t.ai_confidence > 0]
        if len(trades_with_conf) < 10:
            return None

        # 신뢰도 구간별 승률
        low_conf = [t for t in trades_with_conf if t.ai_confidence < 0.6]
        mid_conf = [t for t in trades_with_conf if 0.6 <= t.ai_confidence < 0.8]
        high_conf = [t for t in trades_with_conf if t.ai_confidence >= 0.8]

        results = {}
        for label, group in [("low(<0.6)", low_conf), ("mid(0.6~0.8)", mid_conf), ("high(≥0.8)", high_conf)]:
            if group:
                wr = sum(1 for t in group if t.is_win) / len(group)
                results[label] = {"count": len(group), "win_rate": round(wr * 100, 1)}

        # 저신뢰도 구간의 승률이 낮으면 최소 신뢰도 상향 제안
        if low_conf:
            low_wr = sum(1 for t in low_conf if t.is_win) / len(low_conf)
            if low_wr < 0.35 and len(low_conf) >= 5:
                return {
                    "param": "min_confidence",
                    "issue": f"신뢰도 0.6 미만 거래 승률 {low_wr * 100:.0f}% (낮음)",
                    "suggestion": "최소 신뢰도를 0.6 이상으로 상향 권장",
                    "suggested_value": 0.65,
                    "confidence_breakdown": results,
                }

        return None
