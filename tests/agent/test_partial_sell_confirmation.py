"""부분 매도 체결 판정 검증 — 보유 소멸(전량) vs 수량 감소(부분).

수정 전: '보유목록에서 symbol 소멸'로만 체결 판정 → 부분 매도는 영원히
PENDING_CONFIRM 고착 + FIFO 청산 미실행.
"""
from types import SimpleNamespace

from agent.decision_maker import decision_maker


def _h(qty):
    return SimpleNamespace(symbol="AAA", quantity=qty)


class TestSellFilledByHoldings:
    def test_full_sell_filled_when_holding_gone(self):
        """전량 매도: 보유목록에서 사라지면 체결"""
        assert decision_maker._sell_filled_by_holdings(None, quantity=10, held_qty=10) is True

    def test_full_sell_not_filled_while_holding_remains(self):
        """전량 매도: 아직 보유 중이면 미체결"""
        assert decision_maker._sell_filled_by_holdings(_h(10), quantity=10, held_qty=10) is False

    def test_partial_sell_filled_on_quantity_drop(self):
        """부분 매도(10 중 5): 보유가 5 이하로 줄면 체결"""
        assert decision_maker._sell_filled_by_holdings(_h(5), quantity=5, held_qty=10) is True

    def test_partial_sell_not_filled_without_drop(self):
        """부분 매도: 수량 그대로면 미체결"""
        assert decision_maker._sell_filled_by_holdings(_h(10), quantity=5, held_qty=10) is False

    def test_partial_sell_filled_when_holding_gone(self):
        """부분 매도 주문인데 보유 자체가 사라짐(외부 매도 병합 등) → 체결로 간주"""
        assert decision_maker._sell_filled_by_holdings(None, quantity=5, held_qty=10) is True

    def test_legacy_call_without_held_qty(self):
        """held_qty 미전달(기존 호출부): 소멸 기준만 사용"""
        assert decision_maker._sell_filled_by_holdings(_h(3), quantity=5, held_qty=0) is False
        assert decision_maker._sell_filled_by_holdings(None, quantity=5, held_qty=0) is True
