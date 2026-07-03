"""정점 신호 주입이 보유종목 분석에만 한정되는지 검증"""
import numpy as np
import pandas as pd

from analysis.chart_analyzer import ChartAnalyzer


def _make_dfs():
    """급등 후 소폭 되돌림 + 거래량 정점후 감소 — 정점 신호가 발화하는 전형 패턴"""
    rng = np.random.default_rng(3)
    daily_closes = (10000 + np.cumsum(rng.normal(20, 100, 60))).tolist()
    daily_df = pd.DataFrame({
        "open": daily_closes,
        "high": [c + 100 for c in daily_closes],
        "low": [c - 100 for c in daily_closes],
        "close": daily_closes,
        "volume": [10000] * 60,
    }, dtype=float)

    # 분봉: 고점 찍고 -2% 되돌림, 거래량 피크 후 감소
    minute_closes = [11000, 11200, 11500, 11800, 12000, 11900, 11800, 11750, 11760, 11750]
    minute_vols = [1000, 2000, 5000, 9000, 12000, 6000, 4000, 3000, 2500, 2000]
    minute_df = pd.DataFrame({
        "open": minute_closes,
        "high": [c + 50 for c in minute_closes],
        "low": [c - 50 for c in minute_closes],
        "close": minute_closes,
        "volume": minute_vols,
    }, dtype=float)
    return daily_df, minute_df


def test_peak_signals_injected_for_holdings():
    daily_df, minute_df = _make_dfs()
    result = ChartAnalyzer().analyze(daily_df, minute_df, peak_signals=True)
    assert "[정점 신호]" in result.trend_text


def test_peak_signals_excluded_for_new_candidates():
    """신규 매수 후보 분석 — 정점 신호 미주입 (매수 차단 부작용 방지)"""
    daily_df, minute_df = _make_dfs()
    result = ChartAnalyzer().analyze(daily_df, minute_df, peak_signals=False)
    assert "[정점 신호]" not in result.trend_text
    assert "[정점 신호]" not in result.prompt_text
    # 단, 중립 지표인 '분봉 거래량 추이'(가속/정점 후 둔화)는 신규 후보에도 제공되어야 함
    # — LLM이 진입 조건(거래량 가속 중)을 판정할 유일한 데이터
    assert "분봉 거래량 추이" in result.indicators_text
