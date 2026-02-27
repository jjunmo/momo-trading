"""멀티타임프레임 추세 종합 분석 (순수 알고리즘, LLM 미사용)"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class TrendReport:
    """추세 분석 결과"""
    score: float = 0.0  # -100 ~ +100
    direction: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    strength: str = "WEAK"  # STRONG / MODERATE / WEAK

    # 기간별 추세 기울기 (정규화)
    slope_5d: float = 0.0
    slope_10d: float = 0.0
    slope_20d: float = 0.0
    slope_60d: float = 0.0

    # 추세 정렬도 0~1
    alignment: float = 0.0

    # 이동평균 배열
    ma_arrangement: str = "MIXED"  # BULLISH / BEARISH / MIXED

    # 모멘텀
    momentum: str = "NEUTRAL"  # ACCELERATING / DECELERATING / REVERSING / NEUTRAL

    # 추세 전환 시그널
    reversal_signals: list = field(default_factory=list)

    # 변동성
    volatility_state: str = "NORMAL"  # EXPANDING / CONTRACTING / NORMAL
    atr_ratio: float = 0.0

    # 분봉 기반 당일 추세 (있을 때만)
    intraday: dict | None = None


class TrendAnalyzer:
    """멀티타임프레임 추세 종합 분석"""

    def analyze(
        self, daily_df: pd.DataFrame, minute_df: pd.DataFrame | None = None
    ) -> TrendReport:
        """일봉 + 분봉 추세를 종합 분석"""
        report = TrendReport()

        if daily_df.empty or len(daily_df) < 5:
            return report

        try:
            # 기간별 추세 기울기
            report.slope_5d = self._trend_slope(daily_df, 5)
            report.slope_10d = self._trend_slope(daily_df, 10)
            report.slope_20d = self._trend_slope(daily_df, 20)
            report.slope_60d = self._trend_slope(daily_df, min(60, len(daily_df)))

            # 추세 정렬도
            slopes = [report.slope_5d, report.slope_10d, report.slope_20d, report.slope_60d]
            report.alignment = self._calc_alignment(slopes)

            # 이동평균 배열
            report.ma_arrangement = self._check_ma_arrangement(daily_df)

            # 모멘텀 가속/감속
            report.momentum = self._detect_momentum_shift(daily_df)

            # 추세 전환 시그널
            report.reversal_signals = self._detect_reversal_signals(daily_df)

            # 변동성
            vol_state, atr_ratio = self._analyze_volatility(daily_df)
            report.volatility_state = vol_state
            report.atr_ratio = atr_ratio

            # 분봉 기반 당일 추세
            if minute_df is not None and len(minute_df) >= 10:
                report.intraday = self._analyze_intraday(minute_df)

            # 종합 점수
            report.score = self._compute_trend_score(report)
            if report.score > 30:
                report.direction = "BULLISH"
            elif report.score < -30:
                report.direction = "BEARISH"
            else:
                report.direction = "NEUTRAL"

            if abs(report.score) > 60:
                report.strength = "STRONG"
            elif abs(report.score) > 30:
                report.strength = "MODERATE"
            else:
                report.strength = "WEAK"

        except Exception as e:
            logger.error("추세 분석 오류: {}", str(e))

        return report

    def _trend_slope(self, df: pd.DataFrame, period: int) -> float:
        """선형회귀 기울기 (정규화: 일평균 변화율%)"""
        if len(df) < period:
            period = len(df)
        if period < 3:
            return 0.0

        closes = df["close"].values[-period:].astype(float)
        x = np.arange(len(closes))

        try:
            slope, _ = np.polyfit(x, closes, 1)
            avg_price = np.mean(closes)
            if avg_price == 0:
                return 0.0
            return (slope / avg_price) * 100  # 일평균 변화율%
        except (np.linalg.LinAlgError, ValueError):
            return 0.0

    def _calc_alignment(self, slopes: list[float]) -> float:
        """추세 정렬도: 모든 기간 추세가 같은 방향이면 1.0"""
        if not slopes:
            return 0.0

        signs = [1 if s > 0.05 else (-1 if s < -0.05 else 0) for s in slopes]
        non_zero = [s for s in signs if s != 0]

        if not non_zero:
            return 0.0

        # 같은 방향 비율
        most_common = max(set(non_zero), key=non_zero.count)
        agreement = sum(1 for s in non_zero if s == most_common) / len(slopes)
        return round(agreement, 3)

    def _check_ma_arrangement(self, df: pd.DataFrame) -> str:
        """이동평균 배열 확인"""
        if len(df) < 60:
            if len(df) < 20:
                return "MIXED"
            closes = df["close"]
            sma5 = closes.rolling(5).mean().iloc[-1]
            sma20 = closes.rolling(20).mean().iloc[-1]
            if sma5 > sma20:
                return "BULLISH"
            elif sma5 < sma20:
                return "BEARISH"
            return "MIXED"

        closes = df["close"]
        sma5 = closes.rolling(5).mean().iloc[-1]
        sma10 = closes.rolling(10).mean().iloc[-1]
        sma20 = closes.rolling(20).mean().iloc[-1]
        sma60 = closes.rolling(60).mean().iloc[-1]

        if any(pd.isna(v) for v in [sma5, sma10, sma20, sma60]):
            return "MIXED"

        # 정배열: 5 > 10 > 20 > 60
        if sma5 > sma10 > sma20 > sma60:
            return "BULLISH"
        # 역배열: 5 < 10 < 20 < 60
        if sma5 < sma10 < sma20 < sma60:
            return "BEARISH"
        return "MIXED"

    def _detect_momentum_shift(self, df: pd.DataFrame) -> str:
        """모멘텀 가속/감속 감지"""
        if len(df) < 10:
            return "NEUTRAL"

        recent_slope = self._trend_slope(df, 5)
        prev_slope = self._trend_slope(df.iloc[:-5], 5) if len(df) >= 10 else 0.0

        diff = recent_slope - prev_slope

        if abs(diff) < 0.05:
            return "NEUTRAL"

        # 같은 방향으로 기울기 증가 = 가속
        if recent_slope > 0 and diff > 0.1:
            return "ACCELERATING"
        if recent_slope < 0 and diff < -0.1:
            return "ACCELERATING"

        # 기울기 절대값 감소 = 감속
        if abs(recent_slope) < abs(prev_slope) and abs(diff) > 0.1:
            return "DECELERATING"

        # 방향 전환
        if (recent_slope > 0.05 and prev_slope < -0.05) or \
           (recent_slope < -0.05 and prev_slope > 0.05):
            return "REVERSING"

        return "NEUTRAL"

    def _detect_reversal_signals(self, df: pd.DataFrame) -> list[str]:
        """추세 전환 시그널 감지"""
        signals = []
        if len(df) < 20:
            return signals

        closes = df["close"]

        # SMA 5/20 크로스
        sma5 = closes.rolling(5).mean()
        sma20 = closes.rolling(20).mean()

        if len(sma5) >= 2 and len(sma20) >= 2:
            if not pd.isna(sma5.iloc[-2]) and not pd.isna(sma20.iloc[-2]):
                if sma5.iloc[-2] < sma20.iloc[-2] and sma5.iloc[-1] > sma20.iloc[-1]:
                    signals.append("GOLDEN_CROSS")
                elif sma5.iloc[-2] > sma20.iloc[-2] and sma5.iloc[-1] < sma20.iloc[-1]:
                    signals.append("DEAD_CROSS")

        # 거래량 급증 + 가격 반전
        if "volume" in df.columns and len(df) >= 5:
            vol = df["volume"].values[-5:]
            avg_vol = np.mean(vol[:-1]) if len(vol) > 1 else 0
            if avg_vol > 0 and vol[-1] > avg_vol * 2:
                price_change = (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100
                if abs(price_change) > 2:
                    signals.append(f"VOLUME_SPIKE_{'UP' if price_change > 0 else 'DOWN'}")

        return signals

    def _analyze_volatility(self, df: pd.DataFrame) -> tuple[str, float]:
        """변동성 상태 분석"""
        if len(df) < 14:
            return "NORMAL", 0.0

        # ATR 계산
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        close = df["close"].values.astype(float)

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )

        if len(tr) < 14:
            return "NORMAL", 0.0

        atr_14 = np.mean(tr[-14:])
        avg_price = np.mean(close[-14:])
        atr_ratio = (atr_14 / avg_price * 100) if avg_price > 0 else 0.0

        # 최근 ATR vs 이전 ATR
        if len(tr) >= 28:
            recent_atr = np.mean(tr[-7:])
            prev_atr = np.mean(tr[-21:-7])
            if prev_atr > 0:
                change_ratio = recent_atr / prev_atr
                if change_ratio > 1.3:
                    return "EXPANDING", round(atr_ratio, 3)
                elif change_ratio < 0.7:
                    return "CONTRACTING", round(atr_ratio, 3)

        return "NORMAL", round(atr_ratio, 3)

    def _analyze_intraday(self, minute_df: pd.DataFrame) -> dict:
        """분봉 기반 당일 추세 분석"""
        slope = self._trend_slope(minute_df, len(minute_df))

        # 거래량 추이
        vol_trend = "STABLE"
        if "volume" in minute_df.columns and len(minute_df) >= 6:
            vol = minute_df["volume"].values.astype(float)
            first_half = np.mean(vol[:len(vol)//2])
            second_half = np.mean(vol[len(vol)//2:])
            if first_half > 0:
                ratio = second_half / first_half
                if ratio > 1.5:
                    vol_trend = "INCREASING"
                elif ratio < 0.5:
                    vol_trend = "DECREASING"

        # VWAP 위치
        vwap_position = "AT_VWAP"
        if "volume" in minute_df.columns:
            typical = (minute_df["high"] + minute_df["low"] + minute_df["close"]) / 3
            cumul_tp_vol = (typical * minute_df["volume"]).cumsum()
            cumul_vol = minute_df["volume"].cumsum().replace(0, float("nan"))
            vwap = cumul_tp_vol / cumul_vol
            if not vwap.empty and not pd.isna(vwap.iloc[-1]):
                current = minute_df["close"].iloc[-1]
                vwap_val = vwap.iloc[-1]
                if vwap_val > 0:
                    pct_diff = (current - vwap_val) / vwap_val * 100
                    if pct_diff > 0.5:
                        vwap_position = "ABOVE_VWAP"
                    elif pct_diff < -0.5:
                        vwap_position = "BELOW_VWAP"

        return {
            "slope": round(slope, 4),
            "vol_trend": vol_trend,
            "vwap_position": vwap_position,
            "direction": "BULLISH" if slope > 0.1 else "BEARISH" if slope < -0.1 else "NEUTRAL",
        }

    def _compute_trend_score(self, report: TrendReport) -> float:
        """종합 추세 점수 계산 (-100 ~ +100)"""
        score = 0.0

        # 기간별 기울기 가중 합산 (단기 > 장기)
        score += report.slope_5d * 8
        score += report.slope_10d * 5
        score += report.slope_20d * 3
        score += report.slope_60d * 2

        # 정렬도 보너스
        if report.alignment > 0.7:
            sign = 1 if report.slope_5d > 0 else -1
            score += sign * report.alignment * 15

        # 이동평균 배열
        if report.ma_arrangement == "BULLISH":
            score += 10
        elif report.ma_arrangement == "BEARISH":
            score -= 10

        # 모멘텀
        sign = 1 if report.slope_5d > 0 else -1
        if report.momentum == "ACCELERATING":
            score += sign * 8
        elif report.momentum == "DECELERATING":
            score -= sign * 5
        elif report.momentum == "REVERSING":
            score -= sign * 10

        # 전환 시그널
        for sig in report.reversal_signals:
            if sig == "GOLDEN_CROSS":
                score += 10
            elif sig == "DEAD_CROSS":
                score -= 10

        # 분봉 당일 추세
        if report.intraday:
            intraday_slope = report.intraday.get("slope", 0)
            score += intraday_slope * 3
            if report.intraday.get("vwap_position") == "ABOVE_VWAP":
                score += 3
            elif report.intraday.get("vwap_position") == "BELOW_VWAP":
                score -= 3

        return max(-100, min(100, round(score, 2)))

    def format_for_prompt(self, report: TrendReport) -> str:
        """추세 분석 결과를 프롬프트용 텍스트로 변환"""
        lines = [
            f"- 종합 추세 점수: {report.score:+.1f}/100 ({report.direction}, {report.strength})",
            f"- 추세 기울기: 5일={report.slope_5d:+.3f}%, 10일={report.slope_10d:+.3f}%, "
            f"20일={report.slope_20d:+.3f}%, 60일={report.slope_60d:+.3f}%",
            f"- 추세 정렬도: {report.alignment:.1%}",
            f"- 이동평균 배열: {report.ma_arrangement}",
            f"- 모멘텀: {report.momentum}",
            f"- 변동성: {report.volatility_state} (ATR비율 {report.atr_ratio:.2f}%)",
        ]

        if report.reversal_signals:
            lines.append(f"- 전환 시그널: {', '.join(report.reversal_signals)}")

        if report.intraday:
            intra = report.intraday
            lines.append(
                f"- 당일(분봉): {intra['direction']}, "
                f"기울기={intra['slope']:+.3f}%, "
                f"거래량={intra['vol_trend']}, "
                f"VWAP={intra['vwap_position']}"
            )

        return "\n".join(lines)
