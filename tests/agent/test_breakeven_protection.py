"""본전 보호 — 매입가가 아닌 '비용 반영 본전가'(매입가 + 왕복 수수료·거래세)로 상향 검증.

매입가 그대로 스탑을 잡으면 체결 시 왕복 비용(~0.2%)만큼 실손실.
"""
import pytest

from realtime.event_detector import PriceGuard
from util.pnl_calculator import breakeven_price


@pytest.fixture
def guard(monkeypatch):
    from scheduler.market_calendar import market_calendar
    monkeypatch.setattr(market_calendar, "is_domestic_trading_hours", lambda: True)
    return PriceGuard()


async def test_breakeven_stop_includes_costs(guard):
    """트리거 수익률 도달 → 스탑이 매입가(10,000)가 아닌 비용 반영 본전가로 상향"""
    guard.set_thresholds(
        "AAA", entry_price=10000.0, stop_loss=9700.0,
        trailing_stop_pct=50.0, breakeven_trigger_pct=1.5,
    )
    await guard.on_price_update({"symbol": "AAA", "price": 10200.0})  # +2.0% ≥ 1.5%

    th = guard.get_thresholds("AAA")
    be = breakeven_price(10000.0)
    assert be > 10000.0                # 본전가 자체가 매입가보다 높아야 함
    assert th.stop_loss == be          # 매입가 10,000이 아닌 10,020.28


async def test_breakeven_not_triggered_below_threshold(guard):
    """트리거 미달 수익률에서는 스탑 유지"""
    guard.set_thresholds(
        "AAA", entry_price=10000.0, stop_loss=9700.0,
        trailing_stop_pct=50.0, breakeven_trigger_pct=1.5,
    )
    await guard.on_price_update({"symbol": "AAA", "price": 10100.0})  # +1.0% < 1.5%
    assert guard.get_thresholds("AAA").stop_loss == 9700.0
