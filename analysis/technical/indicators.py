"""기술적 지표 계산 (RSI, MACD, SMA, 볼린저 밴드 등)"""
import pandas as pd
import pandas_ta as ta
from loguru import logger


class TechnicalIndicators:
    """pandas-ta 기반 기술적 지표 계산"""

    @staticmethod
    def calculate_all(df: pd.DataFrame) -> dict:
        """
        모든 기술적 지표를 한 번에 계산

        Args:
            df: OHLCV 데이터프레임 (columns: open, high, low, close, volume)

        Returns:
            dict: 계산된 지표 딕셔너리
        """
        if df.empty or len(df) < 5:
            return {}

        result = {}

        try:
            # RSI (14일)
            rsi = ta.rsi(df["close"], length=14)
            if rsi is not None and not rsi.empty:
                result["rsi_14"] = round(rsi.iloc[-1], 2) if not pd.isna(rsi.iloc[-1]) else None

            # MACD (12, 26, 9)
            macd = ta.macd(df["close"])
            if macd is not None and not macd.empty:
                result["macd"] = round(macd.iloc[-1, 0], 4) if not pd.isna(macd.iloc[-1, 0]) else None
                result["macd_signal"] = round(macd.iloc[-1, 1], 4) if not pd.isna(macd.iloc[-1, 1]) else None
                result["macd_histogram"] = round(macd.iloc[-1, 2], 4) if not pd.isna(macd.iloc[-1, 2]) else None

            # SMA (5, 20, 60일)
            for period in [5, 20, 60]:
                sma = ta.sma(df["close"], length=period)
                if sma is not None and not sma.empty and not pd.isna(sma.iloc[-1]):
                    result[f"sma_{period}"] = round(sma.iloc[-1], 2)

            # EMA (5, 20일)
            for period in [5, 20]:
                ema = ta.ema(df["close"], length=period)
                if ema is not None and not ema.empty and not pd.isna(ema.iloc[-1]):
                    result[f"ema_{period}"] = round(ema.iloc[-1], 2)

            # 볼린저밴드 (20, 2) — pandas-ta 컬럼 순서: [BBL, BBM, BBU, BBB, BBP]
            # 0=Lower, 1=Middle, 2=Upper (이전 코드는 0/2를 뒤바꿔 매핑하던 버그)
            bbands = ta.bbands(df["close"], length=20, std=2)
            if bbands is not None and not bbands.empty:
                result["bb_lower"] = round(bbands.iloc[-1, 0], 2) if not pd.isna(bbands.iloc[-1, 0]) else None
                result["bb_middle"] = round(bbands.iloc[-1, 1], 2) if not pd.isna(bbands.iloc[-1, 1]) else None
                result["bb_upper"] = round(bbands.iloc[-1, 2], 2) if not pd.isna(bbands.iloc[-1, 2]) else None

            # 스토캐스틱 (14, 3, 3)
            stoch = ta.stoch(df["high"], df["low"], df["close"])
            if stoch is not None and not stoch.empty:
                result["stoch_k"] = round(stoch.iloc[-1, 0], 2) if not pd.isna(stoch.iloc[-1, 0]) else None
                result["stoch_d"] = round(stoch.iloc[-1, 1], 2) if not pd.isna(stoch.iloc[-1, 1]) else None

            # ATR (14일)
            atr = ta.atr(df["high"], df["low"], df["close"], length=14)
            if atr is not None and not atr.empty and not pd.isna(atr.iloc[-1]):
                result["atr_14"] = round(atr.iloc[-1], 2)

            # === Phase 7 확장 지표 ===

            # 볼린저밴드 Squeeze 감지 (밴드폭 축소 → 변동성 폭발 전조)
            if bbands is not None and not bbands.empty and len(bbands) >= 20:
                bb_width = bbands.iloc[:, 0] - bbands.iloc[:, 2]  # upper - lower
                if not bb_width.empty:
                    current_width = bb_width.iloc[-1]
                    avg_width = bb_width.iloc[-20:].mean()
                    if avg_width > 0:
                        squeeze_ratio = current_width / avg_width
                        result["bb_squeeze_ratio"] = round(squeeze_ratio, 3)
                        result["bb_squeeze"] = squeeze_ratio < 0.5

            # VWAP (거래량가중평균가격) - 당일 기준
            if "volume" in df.columns and len(df) >= 2:
                typical_price = (df["high"] + df["low"] + df["close"]) / 3
                cumul_tp_vol = (typical_price * df["volume"]).cumsum()
                cumul_vol = df["volume"].cumsum()
                vwap = cumul_tp_vol / cumul_vol.replace(0, float("nan"))
                if not vwap.empty and not pd.isna(vwap.iloc[-1]):
                    result["vwap"] = round(vwap.iloc[-1], 2)

            # 피보나치 되돌림 레벨
            if len(df) >= 20:
                recent_high = df["high"].iloc[-20:].max()
                recent_low = df["low"].iloc[-20:].min()
                fib_range = recent_high - recent_low
                if fib_range > 0:
                    result["fib_0"] = round(recent_low, 2)
                    result["fib_236"] = round(recent_low + fib_range * 0.236, 2)
                    result["fib_382"] = round(recent_low + fib_range * 0.382, 2)
                    result["fib_500"] = round(recent_low + fib_range * 0.500, 2)
                    result["fib_618"] = round(recent_low + fib_range * 0.618, 2)
                    result["fib_1000"] = round(recent_high, 2)

            # 일목균형표 (국내 주식에 유효)
            ichimoku = ta.ichimoku(df["high"], df["low"], df["close"])
            if ichimoku is not None and len(ichimoku) == 2:
                ichi_df = ichimoku[0]
                if ichi_df is not None and not ichi_df.empty:
                    last = ichi_df.iloc[-1]
                    for col in ichi_df.columns:
                        val = last[col]
                        if not pd.isna(val):
                            key = col.replace(" ", "_").lower()
                            result[f"ichimoku_{key}"] = round(val, 2)

            # Williams %R (14일) — 정상 범위 -100~0, 이상치 차단
            willr = ta.willr(df["high"], df["low"], df["close"], length=14)
            if willr is not None and not willr.empty and not pd.isna(willr.iloc[-1]):
                result["williams_r"] = round(max(-100.0, min(0.0, willr.iloc[-1])), 2)

            # CCI (20일) — 직접 계산. pandas_ta 0.4.71b0의 cci는 괄호 누락 버그로
            # (tp - sma/(0.015*md)) ≈ 가격 자체를 반환해 전 종목이 "극단 과매수"로 보임.
            # 분모(MD) 0 근접 폭증(NXT 단일가 세션 등)은 결측 처리 — 클리핑하면
            # "±300 극단 과매수"라는 거짓 신호로 둔갑함
            if len(df) >= 20:
                tp = (df["high"] + df["low"] + df["close"]) / 3
                sma_tp = tp.rolling(20).mean().iloc[-1]
                md = float((tp.tail(20) - tp.tail(20).mean()).abs().mean())
                if not pd.isna(sma_tp) and md > 0:
                    cci_val = (tp.iloc[-1] - sma_tp) / (0.015 * md)
                    if abs(cci_val) <= 500:
                        result["cci_20"] = round(cci_val, 2)

            # OBV (On Balance Volume)
            obv = ta.obv(df["close"], df["volume"])
            if obv is not None and not obv.empty and not pd.isna(obv.iloc[-1]):
                result["obv"] = int(obv.iloc[-1])
                # OBV 추세 (최근 5일 기울기)
                if len(obv) >= 5:
                    obv_slope = obv.iloc[-1] - obv.iloc[-5]
                    result["obv_trend"] = "rising" if obv_slope > 0 else "falling"

            # ADX (추세 강도, 14일) — 정상 범위 0~100
            adx = ta.adx(df["high"], df["low"], df["close"], length=14)
            if adx is not None and not adx.empty:
                adx_val = adx.iloc[-1, 0] if not pd.isna(adx.iloc[-1, 0]) else None
                if adx_val is not None:
                    adx_val = max(0.0, min(100.0, adx_val))
                    result["adx_14"] = round(adx_val, 2)
                    if adx_val >= 25:
                        result["trend_strength"] = "strong"
                    else:
                        result["trend_strength"] = "weak"

            # MFI (Money Flow Index, 14일) — 정의상 0~100
            mfi = ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=14)
            if mfi is not None and not mfi.empty and not pd.isna(mfi.iloc[-1]):
                result["mfi_14"] = round(max(0.0, min(100.0, mfi.iloc[-1])), 2)

            # 현재가 vs 이동평균 위치
            current_price = df["close"].iloc[-1]
            result["current_price"] = round(current_price, 2)

            sma_20 = result.get("sma_20")
            if sma_20:
                result["price_vs_sma20"] = "above" if current_price > sma_20 else "below"

            # 골든크로스/데드크로스 감지
            sma_5 = result.get("sma_5")
            sma_20_val = result.get("sma_20")
            if sma_5 and sma_20_val:
                prev_sma5 = ta.sma(df["close"], length=5)
                prev_sma20 = ta.sma(df["close"], length=20)
                if prev_sma5 is not None and prev_sma20 is not None and len(prev_sma5) >= 2:
                    if (prev_sma5.iloc[-2] < prev_sma20.iloc[-2] and
                            prev_sma5.iloc[-1] > prev_sma20.iloc[-1]):
                        result["cross_signal"] = "GOLDEN_CROSS"
                    elif (prev_sma5.iloc[-2] > prev_sma20.iloc[-2] and
                          prev_sma5.iloc[-1] < prev_sma20.iloc[-1]):
                        result["cross_signal"] = "DEAD_CROSS"

        except Exception as e:
            logger.error("기술적 지표 계산 오류: {}", str(e))

        return result

    @staticmethod
    def calculate_minute(minute_df: pd.DataFrame) -> dict:
        """분봉 데이터 기반 지표 계산 — AI가 review_interval_min 정확 산출하도록 지원

        Args:
            minute_df: 분봉 OHLCV (columns: open, high, low, close, volume)

        Returns:
            dict: atr_minute_14, minute_volatility_pct, minute_rsi_14 등
                  데이터 부족 시 해당 필드 누락 (pandas-ta NaN 반환)
        """
        if minute_df is None or minute_df.empty or len(minute_df) < 5:
            return {}

        result: dict = {}

        try:
            # 데이터 퇴화 감지 — NXT 단일가/거래정지: 봉 범위·종가 변화 모두 0 근접.
            # 퇴화 데이터로 ATR=0, RSI 고정 등 거짓 신호를 LLM에 주지 않도록 지표 산출 생략
            recent = minute_df.tail(10)
            last_close = float(recent["close"].iloc[-1]) if len(recent) else 0
            if last_close > 0:
                movement = float(
                    (recent["high"] - recent["low"]).abs().sum()
                    + recent["close"].diff().abs().sum()
                )
                if movement / last_close < 0.0005:
                    return {
                        "minute_candle_count": len(minute_df),
                        "minute_data_note": (
                            "퇴화(단일가 세션) — ATR/RSI/변동률 산출 불가, 판단 근거에서 제외"
                        ),
                    }

            # 분봉 ATR(14) — 절대값(원). review_interval_min 계산 1차 기준
            if len(minute_df) >= 14:
                atr_m = ta.atr(minute_df["high"], minute_df["low"], minute_df["close"], length=14)
                if (atr_m is not None and not atr_m.empty
                        and not pd.isna(atr_m.iloc[-1]) and atr_m.iloc[-1] > 0):
                    result["atr_minute_14"] = round(atr_m.iloc[-1], 2)

            # 분봉 평균 변동률 — 최근 14개 캔들의 abs(pct_change) 평균 (%)
            if len(minute_df) >= 15:
                pct = minute_df["close"].pct_change().abs()
                avg_pct = pct.tail(14).mean()
                if not pd.isna(avg_pct):
                    result["minute_volatility_pct"] = round(avg_pct * 100, 3)

            # 분봉 RSI(14) — 단기 과매수/과매도 (일봉 RSI와 보완)
            if len(minute_df) >= 15:
                rsi_m = ta.rsi(minute_df["close"], length=14)
                if rsi_m is not None and not rsi_m.empty and not pd.isna(rsi_m.iloc[-1]):
                    result["minute_rsi_14"] = round(rsi_m.iloc[-1], 2)

            # 분봉 거래량 가속/둔화 — 프롬프트의 가속/정점 판정 기준과 동일 (최근 5봉 vs 직전 5봉).
            # 이 데이터가 없으면 LLM이 진입 조건(거래량 가속 중)을 판정할 근거가 없음
            if len(minute_df) >= 10 and "volume" in minute_df.columns:
                vols = minute_df["volume"].tail(10)
                prev5 = float(vols.head(5).mean())
                last5 = float(vols.tail(5).mean())
                if prev5 > 0:
                    ratio = last5 / prev5
                    if ratio > 1.1:
                        result["minute_volume_trend"] = (
                            f"가속 중 (최근5봉 평균 {last5:,.0f} > 직전5봉 {prev5:,.0f})"
                        )
                    elif ratio < 0.9:
                        result["minute_volume_trend"] = (
                            f"정점 후 둔화 (최근5봉 평균 {last5:,.0f} < 직전5봉 {prev5:,.0f})"
                        )
                    else:
                        result["minute_volume_trend"] = "보합"

            # 최근 분봉 수 — AI가 데이터 신뢰도 판단 가능
            result["minute_candle_count"] = len(minute_df)

        except Exception as e:
            logger.debug("분봉 지표 계산 오류 (무시): {}", str(e))

        return result

    @staticmethod
    def format_for_prompt(indicators: dict) -> str:
        """지표 딕셔너리를 프롬프트용 문자열로 변환"""
        if not indicators:
            return "지표 데이터 없음"

        lines = []
        mapping = {
            "rsi_14": "RSI(14)",
            "macd": "MACD",
            "macd_signal": "MACD Signal",
            "macd_histogram": "MACD Histogram",
            "sma_5": "SMA(5)",
            "sma_20": "SMA(20)",
            "sma_60": "SMA(60)",
            "ema_5": "EMA(5)",
            "ema_20": "EMA(20)",
            "bb_upper": "볼린저 상단",
            "bb_middle": "볼린저 중간",
            "bb_lower": "볼린저 하단",
            "stoch_k": "Stochastic %K",
            "stoch_d": "Stochastic %D",
            "atr_14": "ATR(14, 일봉)",
            "atr_minute_14": "분봉 ATR(14, 절대값 원)",
            "minute_volatility_pct": "분봉 평균 변동률(%)",
            "minute_rsi_14": "분봉 RSI(14)",
            "minute_volume_trend": "분봉 거래량 추이(최근5봉 vs 직전5봉)",
            "minute_candle_count": "분봉 데이터 수",
            "minute_data_note": "분봉 데이터 상태",
            "price_vs_sma20": "가격 vs SMA(20)",
            "cross_signal": "크로스 시그널",
            "bb_squeeze_ratio": "볼린저 Squeeze 비율",
            "bb_squeeze": "볼린저 Squeeze 여부",
            "vwap": "VWAP",
            "fib_236": "피보나치 23.6%",
            "fib_382": "피보나치 38.2%",
            "fib_500": "피보나치 50.0%",
            "fib_618": "피보나치 61.8%",
            "williams_r": "Williams %R",
            "cci_20": "CCI(20)",
            "obv_trend": "OBV 추세",
            "adx_14": "ADX(14)",
            "trend_strength": "추세 강도",
            "mfi_14": "MFI(14)",
        }

        for key, label in mapping.items():
            value = indicators.get(key)
            if value is not None:
                lines.append(f"- {label}: {value}")

        return "\n".join(lines) if lines else "지표 데이터 없음"

    @staticmethod
    def daily_data_to_dataframe(daily_data: list) -> pd.DataFrame:
        """MarketDataDaily 리스트를 DataFrame으로 변환"""
        if not daily_data:
            return pd.DataFrame()

        records = []
        for d in daily_data:
            records.append({
                "date": d.trade_date,
                "open": d.open,
                "high": d.high,
                "low": d.low,
                "close": d.close,
                "volume": d.volume,
            })

        df = pd.DataFrame(records)
        df = df.sort_values("date").reset_index(drop=True)
        return df
