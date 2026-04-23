"""차트 종합 분석 코디네이터 - 기술 지표 + 차트 패턴 + 추세 분석 통합"""
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from analysis.technical.chart_patterns import ChartPatterns
from analysis.technical.indicators import TechnicalIndicators
from analysis.technical.trend_analyzer import TrendAnalyzer, TrendReport


@dataclass
class ChartAnalysisResult:
    """차트 종합 분석 결과"""
    indicators: dict = field(default_factory=dict)
    patterns: dict = field(default_factory=dict)
    trend: TrendReport = field(default_factory=TrendReport)
    signal_summary: dict = field(default_factory=dict)
    prompt_text: str = ""
    patterns_text: str = ""
    indicators_text: str = ""
    trend_text: str = ""


class ChartAnalyzer:
    """차트 종합 분석 코디네이터"""

    def __init__(self):
        self.trend_analyzer = TrendAnalyzer()

    def analyze(
        self, daily_df: pd.DataFrame, minute_df: pd.DataFrame | None = None
    ) -> ChartAnalysisResult:
        """기술적 지표 + 차트 패턴 + 추세 분석을 통합"""
        result = ChartAnalysisResult()

        if daily_df.empty or len(daily_df) < 5:
            return result

        try:
            result.indicators = TechnicalIndicators.calculate_all(daily_df)
            # 분봉 지표 병합 — AI가 review_interval_min 계산 시 분봉 ATR 사용
            if minute_df is not None and not minute_df.empty:
                result.indicators.update(TechnicalIndicators.calculate_minute(minute_df))
            result.patterns = ChartPatterns.detect_all(daily_df)
            result.trend = self.trend_analyzer.analyze(daily_df, minute_df)
            result.signal_summary = self._aggregate_signals(
                result.indicators, result.patterns, result.trend
            )

            result.indicators_text = TechnicalIndicators.format_for_prompt(result.indicators)
            result.patterns_text = ChartPatterns.format_for_prompt(result.patterns)
            result.trend_text = self.trend_analyzer.format_for_prompt(result.trend)
            result.prompt_text = self._format_full_prompt(result)
        except Exception as e:
            logger.error("차트 종합 분석 오류: {}", str(e))

        return result

    def _aggregate_signals(
        self, indicators: dict, patterns: dict, trend: TrendReport
    ) -> dict:
        """모든 시그널을 종합하여 방향성 판단"""
        bullish_score = 0
        bearish_score = 0

        # RSI
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            if rsi < 30:
                bullish_score += 2
            elif rsi > 70:
                bearish_score += 2

        # MACD
        macd_hist = indicators.get("macd_histogram")
        if macd_hist is not None:
            if macd_hist > 0:
                bullish_score += 1
            elif macd_hist < 0:
                bearish_score += 1

        # 크로스 시그널
        cross = indicators.get("cross_signal")
        if cross == "GOLDEN_CROSS":
            bullish_score += 2
        elif cross == "DEAD_CROSS":
            bearish_score += 2

        # 볼린저 Squeeze
        if indicators.get("bb_squeeze"):
            pass  # 방향 미확정, 돌파 대기

        # 스토캐스틱
        stoch_k = indicators.get("stoch_k")
        if stoch_k is not None:
            if stoch_k < 20:
                bullish_score += 1
            elif stoch_k > 80:
                bearish_score += 1

        # 패턴 점수
        weight_map = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        for p in patterns.get("patterns", []):
            w = weight_map.get(p.get("reliability", "LOW"), 1)
            if p.get("signal") == "BULLISH":
                bullish_score += w
            elif p.get("signal") == "BEARISH":
                bearish_score += w

        # 추세 점수 (가장 비중 높음)
        trend_score = trend.score
        if trend_score > 30:
            bullish_score += 4
        elif trend_score > 10:
            bullish_score += 2
        elif trend_score < -30:
            bearish_score += 4
        elif trend_score < -10:
            bearish_score += 2

        net = bullish_score - bearish_score
        if net > 3:
            direction = "BULLISH"
        elif net < -3:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return {
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
            "net_score": net,
            "direction": direction,
            "confidence": min(abs(net) / 15, 1.0),
        }

    def _format_full_prompt(self, result: ChartAnalysisResult) -> str:
        """전체 분석 결과를 프롬프트용 텍스트로"""
        summary = result.signal_summary
        lines = [
            "=== 차트 종합 분석 ===",
            f"시그널 방향: {summary.get('direction', 'NEUTRAL')} "
            f"(매수 {summary.get('bullish_score', 0)} / 매도 {summary.get('bearish_score', 0)} / "
            f"신뢰도 {summary.get('confidence', 0):.0%})",
            "",
            "[기술 지표]",
            result.indicators_text,
            "",
            "[차트 패턴]",
            result.patterns_text,
            "",
            "[추세 분석]",
            result.trend_text,
        ]
        return "\n".join(lines)


chart_analyzer = ChartAnalyzer()
