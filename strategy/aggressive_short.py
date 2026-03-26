"""공격형 단기 전략: 모멘텀, 급등주, 고수익 추구

판단(BUY/SELL/HOLD)은 AI 분석 결과를 신뢰하고,
전략은 실행 파라미터(손절/익절/긴급도)와 이유 텍스트를 제공한다.
"""
from strategy.signal import TradeSignal
from trading.enums import SignalAction, SignalUrgency


class AggressiveShortStrategy:
    """
    공격형 단기 매매 전략 (AGGRESSIVE_SHORT)
    - 대상: 모멘텀 급등주, 거래량 급증 종목
    - 보유 기간: 수시간~3일
    - 손절: -4%, 익절: +8% (기본값, 시장 국면별 동적 조정)
    - 판단: AI recommendation + confidence 기반
    """

    strategy_type = "AGGRESSIVE_SHORT"

    REGIME_PARAMS = {
        "BULL":  {"stop_loss_pct": -4.0, "take_profit_pct": 10.0},
        "THEME": {"stop_loss_pct": -5.0, "take_profit_pct": 12.0},
        "BEAR":  {"stop_loss_pct": -3.0, "take_profit_pct": 6.0},
    }

    def __init__(
        self,
        stop_loss_pct: float = -4.0,
        take_profit_pct: float = 8.0,
        min_confidence: float = 0.55,
    ):
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.min_confidence = min_confidence

    async def evaluate(self, analysis: dict, market_regime: str = "") -> TradeSignal | None:
        """AI 분석 결과 기반 시그널 생성 — 실행 파라미터 제공"""
        recommendation = analysis.get("recommendation", "HOLD")
        confidence = analysis.get("confidence", 0)
        indicators = analysis.get("indicators", {})
        symbol = analysis.get("symbol", "")
        stock_id = analysis.get("stock_id", "")
        current_price = analysis.get("current_price", 0)

        # 국면별 동적 파라미터 (기본값 폴백)
        params = self.REGIME_PARAMS.get(market_regime, {})
        eff_stop = params.get("stop_loss_pct", self.stop_loss_pct)
        eff_target = params.get("take_profit_pct", self.take_profit_pct)

        # 최소 신뢰도 미달 → 스킵 (SELL은 면제)
        if confidence < self.min_confidence and recommendation != "SELL":
            return None

        # HOLD → 스킵
        if recommendation == "HOLD":
            return None

        # 차트 분석 정보 (이유 텍스트용)
        chart_result = analysis.get("chart_result")
        trend_momentum = "NEUTRAL"
        trend_alignment = 0.0
        if chart_result:
            trend = getattr(chart_result, "trend", None)
            if trend:
                trend_momentum = trend.momentum
                trend_alignment = trend.alignment

        macd_hist = indicators.get("macd_histogram")
        cross_signal = indicators.get("cross_signal")

        # === BUY ===
        if recommendation == "BUY":
            reasons = [f"AI 매수 추천 (신뢰도 {confidence:.0%})"]

            # 지표 기반 이유 보강 (판단 차단 아님, 로깅용)
            if macd_hist and macd_hist > 0:
                reasons.append(f"MACD 양전환({macd_hist:.4f})")
            if cross_signal == "GOLDEN_CROSS":
                reasons.append("골든크로스 감지")
            if trend_momentum == "ACCELERATING":
                reasons.append("모멘텀 가속 중")
            elif trend_momentum == "DECELERATING":
                reasons.append("모멘텀 감속 주의")
            if trend_alignment >= 0.75:
                reasons.append(f"추세 정렬도 {trend_alignment:.0%}")

            target_price = current_price * (1 + eff_target / 100)
            stop_loss = current_price * (1 + eff_stop / 100)

            return TradeSignal(
                symbol=symbol,
                stock_id=stock_id,
                action=SignalAction.BUY,
                strength=confidence,
                suggested_price=current_price,
                target_price=target_price,
                stop_loss_price=stop_loss,
                urgency=SignalUrgency.IMMEDIATE,
                strategy_type=self.strategy_type,
                reason=" + ".join(reasons),
                confidence=confidence,
            )

        # === SELL ===
        if recommendation == "SELL" or cross_signal == "DEAD_CROSS":
            reasons = []
            if recommendation == "SELL":
                reasons.append(f"AI 매도 추천 (신뢰도 {confidence:.0%})")
            if cross_signal == "DEAD_CROSS":
                reasons.append("데드크로스 감지")

            target_price = current_price * (1 + eff_stop / 100)
            stop_loss = current_price * (1 - eff_stop / 100)

            return TradeSignal(
                symbol=symbol,
                stock_id=stock_id,
                action=SignalAction.SELL,
                strength=confidence if recommendation == "SELL" else 0.6,
                suggested_price=current_price,
                target_price=target_price,
                stop_loss_price=stop_loss,
                urgency=SignalUrgency.IMMEDIATE,
                strategy_type=self.strategy_type,
                reason=" + ".join(reasons),
                confidence=confidence,
            )

        return None
