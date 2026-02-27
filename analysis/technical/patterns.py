"""차트 패턴 인식 - 캔들 패턴, 지지/저항선, 추세선, 거래량 패턴, 다이버전스"""
import numpy as np
import pandas as pd
import pandas_ta as ta
from loguru import logger


class ChartPatterns:
    """차트 패턴 종합 분석"""

    @staticmethod
    def detect_all(df: pd.DataFrame) -> dict:
        """모든 패턴을 한 번에 감지하여 딕셔너리로 반환"""
        if df.empty or len(df) < 10:
            return {"patterns": [], "support_resistance": {}, "trend": {}, "summary": "데이터 부족"}

        patterns = ChartPatterns.detect_patterns(df)
        candles = ChartPatterns.detect_candle_patterns(df)
        sr = ChartPatterns.detect_support_resistance(df)
        trend = ChartPatterns.detect_trend(df)
        vol_patterns = ChartPatterns.detect_volume_patterns(df)
        divergence = ChartPatterns.detect_divergence(df)

        all_patterns = patterns + candles + vol_patterns + divergence

        return {
            "patterns": all_patterns,
            "support_resistance": sr,
            "trend": trend,
            "summary": ChartPatterns._build_summary(all_patterns, sr, trend),
        }

    # ── 기본 패턴 ──

    @staticmethod
    def detect_patterns(df: pd.DataFrame) -> list[dict]:
        """기본 차트 패턴 감지"""
        if df.empty or len(df) < 10:
            return []

        patterns = []

        try:
            consecutive = ChartPatterns._detect_consecutive(df)
            if consecutive:
                patterns.append(consecutive)

            vol_climax = ChartPatterns._detect_volume_climax(df)
            if vol_climax:
                patterns.append(vol_climax)

        except Exception as e:
            logger.error("패턴 감지 오류: {}", str(e))

        return patterns

    @staticmethod
    def _detect_consecutive(df: pd.DataFrame) -> dict | None:
        """연속 상승/하락 감지"""
        changes = df["close"].diff().dropna()
        if changes.empty:
            return None

        ups = 0
        for c in reversed(changes.values):
            if c > 0:
                ups += 1
            else:
                break
        if ups >= 3:
            return {"pattern": "CONSECUTIVE_UP", "days": ups,
                    "description": f"{ups}일 연속 상승", "signal": "BULLISH"}

        downs = 0
        for c in reversed(changes.values):
            if c < 0:
                downs += 1
            else:
                break
        if downs >= 3:
            return {"pattern": "CONSECUTIVE_DOWN", "days": downs,
                    "description": f"{downs}일 연속 하락", "signal": "BEARISH"}

        return None

    @staticmethod
    def _detect_volume_climax(df: pd.DataFrame) -> dict | None:
        """거래량 클라이맥스 감지"""
        if "volume" not in df.columns or len(df) < 10:
            return None

        avg_vol = df["volume"].iloc[-11:-1].mean()
        last_vol = df["volume"].iloc[-1]

        if avg_vol > 0 and last_vol > avg_vol * 3:
            return {"pattern": "VOLUME_CLIMAX", "ratio": round(last_vol / avg_vol, 2),
                    "description": f"거래량 폭발 (평균 대비 {last_vol / avg_vol:.1f}배)",
                    "signal": "ATTENTION"}
        return None

    # ── 캔들 패턴 인식 ──

    @staticmethod
    def detect_candle_patterns(df: pd.DataFrame) -> list[dict]:
        """캔들스틱 패턴 인식 (최근 3개 봉 분석)"""
        if len(df) < 3:
            return []

        patterns = []
        o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values

        try:
            # 망치형 (Hammer) - 하락 추세 후 긴 아래꼬리
            if ChartPatterns._is_hammer(o[-1], h[-1], l[-1], c[-1]):
                trend_down = c[-3] > c[-2] > c[-1] if len(df) >= 3 else False
                if trend_down or c[-2] > c[-1]:
                    patterns.append({"pattern": "HAMMER", "description": "망치형 (반등 시그널)",
                                     "signal": "BULLISH", "reliability": "MEDIUM"})

            # 역망치형 (Inverted Hammer)
            if ChartPatterns._is_inverted_hammer(o[-1], h[-1], l[-1], c[-1]):
                patterns.append({"pattern": "INVERTED_HAMMER", "description": "역망치형",
                                 "signal": "BULLISH", "reliability": "LOW"})

            # 교수형 (Hanging Man) - 상승 추세 후 긴 아래꼬리
            if ChartPatterns._is_hammer(o[-1], h[-1], l[-1], c[-1]):
                trend_up = c[-3] < c[-2] < c[-1] if len(df) >= 3 else False
                if trend_up:
                    patterns.append({"pattern": "HANGING_MAN", "description": "교수형 (하락 전환 경고)",
                                     "signal": "BEARISH", "reliability": "MEDIUM"})

            # 도지 (Doji) - 시가 ≈ 종가
            if ChartPatterns._is_doji(o[-1], h[-1], l[-1], c[-1]):
                patterns.append({"pattern": "DOJI", "description": "도지 (추세 전환 가능)",
                                 "signal": "NEUTRAL", "reliability": "MEDIUM"})

            # 장악형 (Engulfing)
            if len(df) >= 2:
                engulf = ChartPatterns._detect_engulfing(o[-2], h[-2], l[-2], c[-2],
                                                         o[-1], h[-1], l[-1], c[-1])
                if engulf:
                    patterns.append(engulf)

            # 샛별/저녁별 (Morning/Evening Star)
            if len(df) >= 3:
                star = ChartPatterns._detect_star(o[-3], c[-3], o[-2], c[-2], o[-1], c[-1])
                if star:
                    patterns.append(star)

        except Exception as e:
            logger.error("캔들 패턴 감지 오류: {}", str(e))

        return patterns

    @staticmethod
    def _is_hammer(o: float, h: float, l: float, c: float) -> bool:
        """망치형 판별 - 아래꼬리가 몸통의 2배 이상"""
        body = abs(c - o)
        if body == 0:
            return False
        lower_shadow = min(o, c) - l
        upper_shadow = h - max(o, c)
        return lower_shadow > body * 2 and upper_shadow < body * 0.5

    @staticmethod
    def _is_inverted_hammer(o: float, h: float, l: float, c: float) -> bool:
        """역망치형 판별 - 위꼬리가 몸통의 2배 이상"""
        body = abs(c - o)
        if body == 0:
            return False
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        return upper_shadow > body * 2 and lower_shadow < body * 0.5

    @staticmethod
    def _is_doji(o: float, h: float, l: float, c: float) -> bool:
        """도지 판별 - 몸통이 전체 범위의 10% 이하"""
        full_range = h - l
        if full_range == 0:
            return False
        body = abs(c - o)
        return body / full_range < 0.1

    @staticmethod
    def _detect_engulfing(o1, h1, l1, c1, o2, h2, l2, c2) -> dict | None:
        """장악형 패턴 감지"""
        body1 = c1 - o1
        body2 = c2 - o2

        # 상승 장악형: 전일 음봉 + 당일 양봉이 전일을 완전히 감쌈
        if body1 < 0 and body2 > 0 and o2 <= c1 and c2 >= o1:
            return {"pattern": "BULLISH_ENGULFING", "description": "상승 장악형 (강한 매수 시그널)",
                    "signal": "BULLISH", "reliability": "HIGH"}

        # 하락 장악형: 전일 양봉 + 당일 음봉이 전일을 완전히 감쌈
        if body1 > 0 and body2 < 0 and o2 >= c1 and c2 <= o1:
            return {"pattern": "BEARISH_ENGULFING", "description": "하락 장악형 (강한 매도 시그널)",
                    "signal": "BEARISH", "reliability": "HIGH"}

        return None

    @staticmethod
    def _detect_star(o1, c1, o2, c2, o3, c3) -> dict | None:
        """샛별/저녁별 패턴 감지"""
        body1 = c1 - o1
        body2 = abs(c2 - o2)
        body3 = c3 - o3

        mid_is_small = body2 < abs(body1) * 0.3

        # 샛별 (Morning Star): 큰 음봉 → 작은 몸통(갭 다운) → 큰 양봉
        if body1 < 0 and body3 > 0 and mid_is_small:
            return {"pattern": "MORNING_STAR", "description": "샛별 (강한 반등 시그널)",
                    "signal": "BULLISH", "reliability": "HIGH"}

        # 저녁별 (Evening Star): 큰 양봉 → 작은 몸통(갭 업) → 큰 음봉
        if body1 > 0 and body3 < 0 and mid_is_small:
            return {"pattern": "EVENING_STAR", "description": "저녁별 (하락 전환 시그널)",
                    "signal": "BEARISH", "reliability": "HIGH"}

        return None

    # ── 지지선/저항선 자동 탐지 ──

    @staticmethod
    def detect_support_resistance(df: pd.DataFrame, window: int = 5) -> dict:
        """지지선/저항선 자동 탐지 (피봇 포인트 기반)"""
        if len(df) < window * 2 + 1:
            return {"supports": [], "resistances": [], "current_price": 0}

        highs = df["high"].values
        lows = df["low"].values
        current_price = df["close"].iloc[-1]

        supports = []
        resistances = []

        # 로컬 최저점 → 지지선
        for i in range(window, len(lows) - window):
            if lows[i] == min(lows[i - window:i + window + 1]):
                supports.append(round(float(lows[i]), 2))

        # 로컬 최고점 → 저항선
        for i in range(window, len(highs) - window):
            if highs[i] == max(highs[i - window:i + window + 1]):
                resistances.append(round(float(highs[i]), 2))

        # 중복 제거 (±1% 이내는 클러스터링)
        supports = ChartPatterns._cluster_levels(sorted(supports), tolerance=0.01)
        resistances = ChartPatterns._cluster_levels(sorted(resistances), tolerance=0.01)

        # 현재가 기준으로 가장 가까운 것만 유지
        nearby_supports = [s for s in supports if s < current_price][-3:]
        nearby_resistances = [r for r in resistances if r > current_price][:3]

        return {
            "supports": nearby_supports,
            "resistances": nearby_resistances,
            "current_price": round(float(current_price), 2),
            "nearest_support": nearby_supports[-1] if nearby_supports else None,
            "nearest_resistance": nearby_resistances[0] if nearby_resistances else None,
        }

    @staticmethod
    def _cluster_levels(levels: list[float], tolerance: float = 0.01) -> list[float]:
        """유사한 가격 수준을 클러스터링"""
        if not levels:
            return []
        clustered = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - clustered[-1]) / clustered[-1] > tolerance:
                clustered.append(lvl)
            else:
                clustered[-1] = (clustered[-1] + lvl) / 2
        return [round(c, 2) for c in clustered]

    # ── 추세 분석 ──

    @staticmethod
    def detect_trend(df: pd.DataFrame) -> dict:
        """추세선 분석 (상승/하락/횡보 판별)"""
        if len(df) < 10:
            return {"direction": "UNKNOWN", "strength": 0}

        closes = df["close"].values
        n = len(closes)

        # 선형 회귀로 추세 방향/강도 판별
        x = np.arange(n)
        slope, intercept = np.polyfit(x, closes, 1)

        # 기울기를 평균 가격 대비 %로 환산
        avg_price = np.mean(closes)
        slope_pct = (slope / avg_price) * 100 if avg_price > 0 else 0

        # R² (결정계수)로 추세 신뢰도
        y_pred = slope * x + intercept
        ss_res = np.sum((closes - y_pred) ** 2)
        ss_tot = np.sum((closes - np.mean(closes)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        if abs(slope_pct) < 0.1:
            direction = "SIDEWAYS"
        elif slope_pct > 0:
            direction = "UPTREND"
        else:
            direction = "DOWNTREND"

        # 단기(5일) vs 중기(20일) 추세 비교
        short_trend = "UNKNOWN"
        if len(df) >= 5:
            short_slope, _ = np.polyfit(np.arange(5), closes[-5:], 1)
            short_pct = (short_slope / avg_price) * 100 if avg_price > 0 else 0
            if abs(short_pct) < 0.1:
                short_trend = "SIDEWAYS"
            elif short_pct > 0:
                short_trend = "UPTREND"
            else:
                short_trend = "DOWNTREND"

        return {
            "direction": direction,
            "slope_pct_per_day": round(slope_pct, 4),
            "r_squared": round(r_squared, 3),
            "strength": round(abs(slope_pct) * r_squared, 4),
            "short_trend": short_trend,
            "trend_alignment": direction == short_trend,
        }

    # ── 거래량 패턴 분석 ──

    @staticmethod
    def detect_volume_patterns(df: pd.DataFrame) -> list[dict]:
        """거래량 패턴 분석"""
        if "volume" not in df.columns or len(df) < 10:
            return []

        patterns = []
        vol = df["volume"].values
        close = df["close"].values

        # 거래량 다이버전스: 가격 상승 + 거래량 감소 (약세 시그널)
        if len(df) >= 5:
            price_up = close[-1] > close[-5]
            vol_down = vol[-1] < np.mean(vol[-5:])
            if price_up and vol_down:
                patterns.append({
                    "pattern": "VOLUME_DIVERGENCE_BEARISH",
                    "description": "거래량 다이버전스 (가격 상승 중 거래량 감소 → 상승 약화)",
                    "signal": "BEARISH", "reliability": "MEDIUM",
                })

            # 가격 하락 + 거래량 감소 (셀링 클라이맥스 종료 가능)
            price_down = close[-1] < close[-5]
            if price_down and vol_down:
                patterns.append({
                    "pattern": "VOLUME_DRY_UP",
                    "description": "거래량 소진 (매도 압력 약화 → 반등 가능)",
                    "signal": "BULLISH", "reliability": "LOW",
                })

        # 거래량 급증 + 양봉 (강한 매수세)
        if len(df) >= 2:
            avg_vol = np.mean(vol[-10:]) if len(vol) >= 10 else np.mean(vol)
            if avg_vol > 0 and vol[-1] > avg_vol * 2 and close[-1] > df["open"].values[-1]:
                patterns.append({
                    "pattern": "HIGH_VOLUME_BULLISH",
                    "description": f"대량 매수봉 (거래량 {vol[-1] / avg_vol:.1f}배)",
                    "signal": "BULLISH", "reliability": "HIGH",
                })

            # 거래량 급증 + 음봉 (강한 매도세)
            if avg_vol > 0 and vol[-1] > avg_vol * 2 and close[-1] < df["open"].values[-1]:
                patterns.append({
                    "pattern": "HIGH_VOLUME_BEARISH",
                    "description": f"대량 매도봉 (거래량 {vol[-1] / avg_vol:.1f}배)",
                    "signal": "BEARISH", "reliability": "HIGH",
                })

        return patterns

    # ── 다이버전스 감지 (RSI, MACD) ──

    @staticmethod
    def detect_divergence(df: pd.DataFrame) -> list[dict]:
        """RSI/MACD 다이버전스 감지"""
        if len(df) < 20:
            return []

        patterns = []

        try:
            close = df["close"]
            rsi = ta.rsi(close, length=14)
            if rsi is None or rsi.empty:
                return []

            # 최근 20봉에서 로컬 저점/고점 찾기
            lookback = min(20, len(df))

            # 강세 다이버전스: 가격 저점 하락 + RSI 저점 상승
            price_lows = []
            rsi_lows = []
            for i in range(len(df) - lookback, len(df) - 1):
                if i >= 1 and close.iloc[i] < close.iloc[i - 1] and close.iloc[i] < close.iloc[i + 1]:
                    price_lows.append((i, close.iloc[i]))
                    rsi_lows.append((i, rsi.iloc[i]))

            if len(price_lows) >= 2:
                p1, p2 = price_lows[-2], price_lows[-1]
                r1, r2 = rsi_lows[-2], rsi_lows[-1]
                if p2[1] < p1[1] and r2[1] > r1[1]:
                    patterns.append({
                        "pattern": "RSI_BULLISH_DIVERGENCE",
                        "description": "RSI 강세 다이버전스 (가격 신저점 + RSI 저점 상승 → 반등 기대)",
                        "signal": "BULLISH", "reliability": "HIGH",
                    })

            # 약세 다이버전스: 가격 고점 상승 + RSI 고점 하락
            price_highs = []
            rsi_highs = []
            for i in range(len(df) - lookback, len(df) - 1):
                if i >= 1 and close.iloc[i] > close.iloc[i - 1] and close.iloc[i] > close.iloc[i + 1]:
                    price_highs.append((i, close.iloc[i]))
                    rsi_highs.append((i, rsi.iloc[i]))

            if len(price_highs) >= 2:
                p1, p2 = price_highs[-2], price_highs[-1]
                r1, r2 = rsi_highs[-2], rsi_highs[-1]
                if p2[1] > p1[1] and r2[1] < r1[1]:
                    patterns.append({
                        "pattern": "RSI_BEARISH_DIVERGENCE",
                        "description": "RSI 약세 다이버전스 (가격 신고점 + RSI 고점 하락 → 하락 전환 주의)",
                        "signal": "BEARISH", "reliability": "HIGH",
                    })

        except Exception as e:
            logger.error("다이버전스 감지 오류: {}", str(e))

        return patterns

    # ── 요약 ──

    @staticmethod
    def _build_summary(patterns: list[dict], sr: dict, trend: dict) -> str:
        """패턴 분석 결과 요약 문자열"""
        lines = []

        # 추세
        direction_kr = {"UPTREND": "상승 추세", "DOWNTREND": "하락 추세", "SIDEWAYS": "횡보"}
        lines.append(f"추세: {direction_kr.get(trend.get('direction', ''), '불명')}")

        # 지지/저항
        ns = sr.get("nearest_support")
        nr = sr.get("nearest_resistance")
        if ns:
            lines.append(f"최근접 지지선: {ns:,.0f}")
        if nr:
            lines.append(f"최근접 저항선: {nr:,.0f}")

        # 주요 패턴
        bullish = [p for p in patterns if p.get("signal") == "BULLISH"]
        bearish = [p for p in patterns if p.get("signal") == "BEARISH"]
        if bullish:
            lines.append(f"매수 시그널: {', '.join(p['description'] for p in bullish[:3])}")
        if bearish:
            lines.append(f"매도 시그널: {', '.join(p['description'] for p in bearish[:3])}")

        return " | ".join(lines) if lines else "특이 패턴 없음"

    @staticmethod
    def format_for_prompt(analysis: dict) -> str:
        """패턴 분석 결과를 프롬프트용 문자열로 변환"""
        if not analysis:
            return "차트 패턴 데이터 없음"

        lines = []

        # 추세
        trend = analysis.get("trend", {})
        if trend:
            direction_kr = {"UPTREND": "상승", "DOWNTREND": "하락", "SIDEWAYS": "횡보", "UNKNOWN": "불명"}
            lines.append(f"[추세] {direction_kr.get(trend.get('direction', ''), '불명')} "
                         f"(일간 기울기: {trend.get('slope_pct_per_day', 0):+.3f}%, "
                         f"신뢰도: {trend.get('r_squared', 0):.2f})")
            if trend.get("short_trend"):
                lines.append(f"  - 단기 추세: {direction_kr.get(trend['short_trend'], '불명')}")

        # 지지/저항
        sr = analysis.get("support_resistance", {})
        if sr.get("supports"):
            lines.append(f"[지지선] {', '.join(f'{s:,.0f}' for s in sr['supports'])}")
        if sr.get("resistances"):
            lines.append(f"[저항선] {', '.join(f'{r:,.0f}' for r in sr['resistances'])}")

        # 패턴
        for p in analysis.get("patterns", []):
            signal_icon = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "─"}.get(p.get("signal", ""), "")
            reliability = p.get("reliability", "")
            lines.append(f"[패턴] {signal_icon} {p['description']} (신뢰도: {reliability})")

        lines.append(f"[요약] {analysis.get('summary', '')}")

        return "\n".join(lines)
