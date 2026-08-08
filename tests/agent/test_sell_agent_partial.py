"""SellAgent 분할 익절 + 러너 전환 검증.

- LLM 판단 매도 + 수익 중 → 절반 매도 + 잔량 러너(take_profit=0, 본전스탑, is_runner)
- 1주 보유 / 손실 중 / 코드 매도(STOP_LOSS 등) → 전량 매도
- 러너 종목에 LLM SELL → 무시(False), 트레일링 스탑은 통과
- +2% 수익 게이트가 LLM 매도 3경로 전체에 적용
"""
from types import SimpleNamespace

import pytest

from agent.sell_agent import SellParams, sell_agent
from realtime.event_detector import event_detector
from tests.conftest import TestAsyncSessionLocal


def _holding(symbol="AAA", quantity=10, pnl_rate=3.0, price=1100.0, avg=1000.0):
    return SimpleNamespace(
        symbol=symbol, name=symbol, quantity=quantity,
        avg_buy_price=avg, current_price=price, pnl_rate=pnl_rate,
    )


@pytest.fixture
def env(monkeypatch):
    """place_order 캡처 + 외부 의존 모두 차단한 매도 실행 환경"""
    event_detector.clear_all()
    orders = []

    async def fake_get_holdings():
        return env_state["holdings"]

    async def fake_place_order(symbol, side, quantity, price=None, market=None):
        orders.append({"symbol": symbol, "side": side, "quantity": quantity})
        return SimpleNamespace(success=True, data={"order_id": "ORD-1"}, error=None)

    async def fake_pending(**kwargs):
        return "pid-1"

    async def fake_wait(**kwargs):
        return True

    async def fake_log(*args, **kwargs):
        return None

    async def fake_stats(days=28):
        return env_state["accuracy_stats"]

    env_state = {"holdings": [_holding()], "accuracy_stats": {}}

    from trading.account_manager import account_manager
    from trading.mcp_client import mcp_client
    from agent.decision_maker import decision_maker
    from services.activity_logger import activity_logger
    from analysis.feedback.judgment_verifier import judgment_verifier
    from scheduler.market_calendar import market_calendar
    import core.database

    monkeypatch.setattr(account_manager, "get_holdings", fake_get_holdings)
    monkeypatch.setattr(mcp_client, "place_order", fake_place_order)
    monkeypatch.setattr(decision_maker, "_create_pending_record", fake_pending)
    monkeypatch.setattr(decision_maker, "wait_for_sell_confirmation", fake_wait)
    monkeypatch.setattr(activity_logger, "log", fake_log)
    monkeypatch.setattr(judgment_verifier, "get_accuracy_stats", fake_stats)
    monkeypatch.setattr(market_calendar, "get_excg_dvsn_cd", lambda: "KRX")
    monkeypatch.setattr(core.database, "AsyncSessionLocal", TestAsyncSessionLocal)

    yield env_state, orders
    event_detector.clear_all()


PROVEN = {"PEAK_SELL": {"n": 20, "hit": 15, "rate": 75.0}}


async def test_partial_sell_and_runner_on_profit(env):
    """수익 중 LLM 매도 → 절반 매도 + 잔량 러너 전환"""
    state, orders = env
    state["holdings"] = [_holding(quantity=10, pnl_rate=3.0)]
    event_detector.set_thresholds(
        "AAA", stop_loss=950.0, take_profit=1100.0,
        trailing_stop_pct=2.0, entry_price=1000.0,
    )

    ok = await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason="TAKE_PROFIT_REVIEW"))
    assert ok is True
    assert orders[0]["quantity"] == 5  # 10 × 0.5

    from util.pnl_calculator import breakeven_price

    th = event_detector.get_thresholds("AAA")
    assert th.is_runner is True
    assert th.take_profit == 0.0          # 익절 재검토 중단
    # 본전 이상 상향 — 비용(왕복 ~0.2%) 반영 본전가 (max(950, breakeven(1000)))
    assert th.stop_loss == breakeven_price(1000.0)
    assert th.trailing_stop_pct == 2.0    # 기존 트레일링 유지


async def test_full_sell_when_single_share(env):
    """1주 보유 → 분할 불가, 전량 매도 폴백"""
    state, orders = env
    state["holdings"] = [_holding(quantity=1, pnl_rate=3.0)]

    ok = await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason="ANALYSIS_SELL"))
    assert ok is True
    assert orders[0]["quantity"] == 1
    assert event_detector.get_thresholds("AAA").is_runner is False  # remove_levels됨 (기본값)


async def test_full_sell_when_losing(env):
    """손실 중 LLM 매도(게이트 해제 상태) → 전량 매도 (러너 전환 없음)"""
    state, orders = env
    state["holdings"] = [_holding(quantity=10, pnl_rate=-1.5)]
    state["accuracy_stats"] = PROVEN  # 게이트 통과용

    ok = await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason="ANALYSIS_SELL"))
    assert ok is True
    assert orders[0]["quantity"] == 10
    assert event_detector.get_thresholds("AAA").is_runner is False


async def test_full_sell_on_code_exit_reason(env):
    """코드 매도(STOP_LOSS)는 분할 미적용 — 전량"""
    state, orders = env
    state["holdings"] = [_holding(quantity=10, pnl_rate=3.0)]

    ok = await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason="STOP_LOSS"))
    assert ok is True
    assert orders[0]["quantity"] == 10


async def test_runner_ignores_llm_sell_but_allows_trailing(env):
    """러너 종목: LLM 매도 무시, 트레일링 스탑 매도는 통과"""
    state, orders = env
    state["holdings"] = [_holding(quantity=5, pnl_rate=8.0)]
    event_detector.set_thresholds("AAA", is_runner=True, stop_loss=1000.0, trailing_stop_pct=3.0)

    for reason in ("ANALYSIS_SELL", "HOLDINGS_REVIEW", "TAKE_PROFIT_REVIEW"):
        assert await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason=reason)) is False
    assert orders == []  # 주문 없음

    ok = await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason="TRAILING_STOP"))
    assert ok is True
    assert orders[0]["quantity"] == 5  # 러너 청산은 전량


async def test_profit_gate_applies_to_all_llm_reasons(env):
    """미입증 상태 + 수익 +2% 미만 → LLM 매도 3경로 모두 차단"""
    state, orders = env
    state["holdings"] = [_holding(quantity=10, pnl_rate=1.0)]
    state["accuracy_stats"] = {}  # 미입증

    for reason in ("ANALYSIS_SELL", "HOLDINGS_REVIEW", "TAKE_PROFIT_REVIEW"):
        assert await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason=reason)) is False
    assert orders == []

    # 코드 매도는 게이트 무관 통과
    assert await sell_agent._execute_sell_locked(SellParams(symbol="AAA", exit_reason="STOP_LOSS")) is True


async def test_explicit_quantity_respected(env):
    """quantity 명시 시 그 수량으로 매도 (보유 초과분은 클램프)"""
    state, orders = env
    state["holdings"] = [_holding(quantity=10, pnl_rate=3.0)]

    ok = await sell_agent._execute_sell_locked(
        SellParams(symbol="AAA", exit_reason="DAY_CLOSE", quantity=99)
    )
    assert ok is True
    assert orders[0]["quantity"] == 10  # min(99, 보유 10)
