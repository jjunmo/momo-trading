"""분봉 퇴화 가드 + CCI 결측 처리 검증"""
import numpy as np
import pandas as pd

from analysis.technical.indicators import TechnicalIndicators


def _make_minute_df(closes: list[float], spread: float = 0.0, volumes: list[int] | None = None):
    n = len(closes)
    closes_s = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "open": closes_s,
        "high": closes_s + spread,
        "low": closes_s - spread,
        "close": closes_s,
        "volume": volumes if volumes is not None else [1000] * n,
    })


def test_degenerate_minute_returns_note_only():
    """NXT 단일가 세션 — 가격 변화 0이면 지표 산출 생략 + 결측 노트 반환"""
    df = _make_minute_df([10000.0] * 30)  # 종가 고정, 고저폭 0
    result = TechnicalIndicators.calculate_minute(df)

    assert result.get("minute_data_note", "").startswith("퇴화")
    assert result["minute_candle_count"] == 30
    assert "atr_minute_14" not in result
    assert "minute_rsi_14" not in result
    assert "minute_volatility_pct" not in result


def test_normal_minute_keeps_indicators():
    """정상 분봉 — 기존 지표 필드 모두 산출, 퇴화 노트 없음"""
    rng = np.random.default_rng(42)
    closes = (10000 + np.cumsum(rng.normal(0, 30, 40))).tolist()
    df = _make_minute_df(closes, spread=20.0)
    result = TechnicalIndicators.calculate_minute(df)

    assert "minute_data_note" not in result
    assert result.get("atr_minute_14", 0) > 0
    assert "minute_rsi_14" in result
    assert "minute_volatility_pct" in result
    assert result.get("minute_volume_trend") == "보합"  # 동일 거래량 → 보합


def test_minute_volume_trend_accel_and_decel():
    """분봉 거래량 추이 — 프롬프트 진입 조건(최근5봉 vs 직전5봉)의 데이터 공급 확인"""
    closes = list(range(10000, 10030))
    accel = TechnicalIndicators.calculate_minute(
        _make_minute_df([float(c) for c in closes], spread=20.0,
                        volumes=[1000] * 20 + [1000, 2000, 3000, 4000, 5000,
                                               8000, 9000, 10000, 11000, 12000]))
    decel = TechnicalIndicators.calculate_minute(
        _make_minute_df([float(c) for c in closes], spread=20.0,
                        volumes=[1000] * 20 + [12000, 11000, 10000, 9000, 8000,
                                               5000, 4000, 3000, 2000, 1000]))
    assert accel["minute_volume_trend"].startswith("가속")
    assert decel["minute_volume_trend"].startswith("정점 후 둔화")


def test_cci_blowup_omitted():
    """일봉 가격이 사실상 고정(분모 MD≈0) → cci_20 폭증값은 결측 처리"""
    n = 30
    closes = [10000.0] * (n - 1) + [10010.0]  # 마지막에 미세 변동 → CCI 폭증
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    }, dtype=float)
    result = TechnicalIndicators.calculate_all(df)

    assert "cci_20" not in result  # 폭증값은 클리핑이 아니라 결측 처리


def test_cci_normal_reported():
    """정상 변동 일봉 → cci_20을 직접 계산식으로 정상 보고 (pandas_ta cci 버그 우회)"""
    rng = np.random.default_rng(7)
    closes = (10000 + np.cumsum(rng.normal(0, 100, 40))).tolist()
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 80 for c in closes],
        "low": [c - 80 for c in closes],
        "close": closes,
        "volume": [1000] * 40,
    }, dtype=float)
    result = TechnicalIndicators.calculate_all(df)

    assert "cci_20" in result
    # 표준 공식 수동 계산과 일치 확인
    tp = (df["high"] + df["low"] + df["close"]) / 3
    md = float((tp.tail(20) - tp.tail(20).mean()).abs().mean())
    expected = (tp.iloc[-1] - tp.rolling(20).mean().iloc[-1]) / (0.015 * md)
    assert result["cci_20"] == round(expected, 2)
    assert abs(result["cci_20"]) <= 500
