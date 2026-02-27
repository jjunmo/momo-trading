"""차트 패턴 감지 (캔들스틱 + 가격 패턴)"""
import numpy as np
import pandas as pd
from loguru import logger


class ChartPatterns:
    """캔들스틱 패턴 및 가격 패턴 감지"""

    @staticmethod
    def detect_all(df: pd.DataFrame) -> dict:
        """모든 패턴 감지 실행"""
        if df.empty or len(df) < 5:
            return {"patterns": [], "trend": "UNKNOWN"}

        patterns = []

        try:
            # 캔들스틱 패턴
            patterns.extend(ChartPatterns._detect_hammer(df))
            patterns.extend(ChartPatterns._detect_engulfing(df))
            patterns.extend(ChartPatterns._detect_doji(df))

            # 가격 패턴
            patterns.extend(ChartPatterns._detect_double_bottom(df))
            patterns.extend(ChartPatterns._detect_double_top(df))

            # 추세
            trend = ChartPatterns._detect_trend(df)
        except Exception as e:
            logger.error("차트 패턴 감지 오류: {}", str(e))
            trend = "UNKNOWN"

        return {"patterns": patterns, "trend": trend}

    @staticmethod
    def _detect_hammer(df: pd.DataFrame) -> list[dict]:
        """망치형/역망치형 캔들 감지"""
        if len(df) < 2:
            return []
        patterns = []
        last = df.iloc[-1]
        body = abs(last["close"] - last["open"])
        full_range = last["high"] - last["low"]

        if full_range == 0:
            return []

        lower_shadow = min(last["open"], last["close"]) - last["low"]
        upper_shadow = last["high"] - max(last["open"], last["close"])

        # 망치형: 하단 꼬리가 몸통의 2배 이상, 상단 꼬리 짧음
        if body > 0 and lower_shadow >= body * 2 and upper_shadow <= body * 0.3:
            patterns.append({
                "name": "HAMMER",
                "signal": "BULLISH",
                "reliability": "MEDIUM",
                "description": "망치형 캔들 - 하락 후 반등 신호",
            })

        # 역망치형
        if body > 0 and upper_shadow >= body * 2 and lower_shadow <= body * 0.3:
            patterns.append({
                "name": "INVERTED_HAMMER",
                "signal": "BULLISH",
                "reliability": "LOW",
                "description": "역망치형 캔들 - 매수 압력 증가 신호",
            })

        return patterns

    @staticmethod
    def _detect_engulfing(df: pd.DataFrame) -> list[dict]:
        """장악형 캔들 감지"""
        if len(df) < 2:
            return []
        patterns = []
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        prev_body = prev["close"] - prev["open"]
        curr_body = curr["close"] - curr["open"]

        # 상승 장악형: 전일 음봉 + 당일 양봉이 전일 몸통을 완전히 감쌈
        if (prev_body < 0 and curr_body > 0 and
                curr["open"] <= prev["close"] and curr["close"] >= prev["open"]):
            patterns.append({
                "name": "BULLISH_ENGULFING",
                "signal": "BULLISH",
                "reliability": "HIGH",
                "description": "상승 장악형 - 강한 매수 전환 신호",
            })

        # 하락 장악형
        if (prev_body > 0 and curr_body < 0 and
                curr["open"] >= prev["close"] and curr["close"] <= prev["open"]):
            patterns.append({
                "name": "BEARISH_ENGULFING",
                "signal": "BEARISH",
                "reliability": "HIGH",
                "description": "하락 장악형 - 강한 매도 전환 신호",
            })

        return patterns

    @staticmethod
    def _detect_doji(df: pd.DataFrame) -> list[dict]:
        """도지 캔들 감지"""
        if len(df) < 1:
            return []
        patterns = []
        last = df.iloc[-1]
        body = abs(last["close"] - last["open"])
        full_range = last["high"] - last["low"]

        if full_range == 0:
            return []

        # 도지: 몸통이 전체 범위의 5% 이하
        if body / full_range < 0.05:
            patterns.append({
                "name": "DOJI",
                "signal": "NEUTRAL",
                "reliability": "MEDIUM",
                "description": "도지 캔들 - 추세 전환 가능성, 방향 미확정",
            })

        return patterns

    @staticmethod
    def _detect_double_bottom(df: pd.DataFrame) -> list[dict]:
        """이중 바닥 패턴 감지"""
        if len(df) < 20:
            return []

        lows = df["low"].values[-20:]
        min_idx_1 = np.argmin(lows[:10])
        min_idx_2 = np.argmin(lows[10:]) + 10

        low_1 = lows[min_idx_1]
        low_2 = lows[min_idx_2]

        if low_1 == 0:
            return []

        # 두 저점이 비슷한 수준 (2% 이내)
        if abs(low_1 - low_2) / low_1 < 0.02 and min_idx_2 - min_idx_1 >= 5:
            # 그리고 그 사이에 반등이 있었어야 함
            mid_high = np.max(lows[min_idx_1:min_idx_2])
            if mid_high > low_1 * 1.02:
                return [{
                    "name": "DOUBLE_BOTTOM",
                    "signal": "BULLISH",
                    "reliability": "HIGH",
                    "description": "이중 바닥 - 강한 지지선 확인, 반등 기대",
                }]

        return []

    @staticmethod
    def _detect_double_top(df: pd.DataFrame) -> list[dict]:
        """이중 천정 패턴 감지"""
        if len(df) < 20:
            return []

        highs = df["high"].values[-20:]
        max_idx_1 = np.argmax(highs[:10])
        max_idx_2 = np.argmax(highs[10:]) + 10

        high_1 = highs[max_idx_1]
        high_2 = highs[max_idx_2]

        if high_1 == 0:
            return []

        if abs(high_1 - high_2) / high_1 < 0.02 and max_idx_2 - max_idx_1 >= 5:
            mid_low = np.min(highs[max_idx_1:max_idx_2])
            if mid_low < high_1 * 0.98:
                return [{
                    "name": "DOUBLE_TOP",
                    "signal": "BEARISH",
                    "reliability": "HIGH",
                    "description": "이중 천정 - 강한 저항선 확인, 하락 경고",
                }]

        return []

    @staticmethod
    def _detect_trend(df: pd.DataFrame, period: int = 20) -> str:
        """선형회귀 기반 추세 판별"""
        if len(df) < period:
            period = len(df)
        if period < 5:
            return "UNKNOWN"

        closes = df["close"].values[-period:]
        x = np.arange(len(closes))
        slope, _ = np.polyfit(x, closes, 1)

        avg_price = np.mean(closes)
        if avg_price == 0:
            return "SIDEWAYS"

        norm_slope = slope / avg_price * 100

        if norm_slope > 0.3:
            return "UPTREND"
        elif norm_slope < -0.3:
            return "DOWNTREND"
        return "SIDEWAYS"

    @staticmethod
    def format_for_prompt(analysis: dict) -> str:
        """차트 패턴 분석 결과를 프롬프트용 텍스트로 변환"""
        if not analysis:
            return "차트 패턴 데이터 없음"

        lines = []
        trend = analysis.get("trend", "UNKNOWN")
        lines.append(f"- 추세: {trend}")

        patterns = analysis.get("patterns", [])
        if patterns:
            for p in patterns:
                signal = p.get("signal", "")
                reliability = p.get("reliability", "")
                desc = p.get("description", p.get("name", ""))
                lines.append(f"- [{signal}/{reliability}] {desc}")
        else:
            lines.append("- 특이 패턴 미감지")

        return "\n".join(lines)
