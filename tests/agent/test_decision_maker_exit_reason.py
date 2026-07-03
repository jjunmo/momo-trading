"""A1 회귀 테스트: 매도 PENDING_CONFIRM 레코드가 exit_reason을 보존하는지.

수정 전: _create_pending_record가 exit_reason을 저장하지 않아 항상 ""로 남고,
복구 sweep(check_pending_sells)의 `tr.exit_reason or "RECOVERED"`가 RECOVERED로 덮어씀.
수정 후: 생성 시점에 실제 사유(DAY_CLOSE 등)를 저장 → 복구 경로가 그대로 보존.
"""
import pytest

from agent import decision_maker as dm_module
from agent.decision_maker import decision_maker
from repositories.trade_result_repository import TradeResultRepository
from tests.conftest import TestAsyncSessionLocal


@pytest.fixture
def patch_session(monkeypatch):
    # decision_maker는 DI가 아닌 모듈 전역 AsyncSessionLocal을 직접 사용 → 테스트 세션으로 교체
    monkeypatch.setattr(dm_module, "AsyncSessionLocal", TestAsyncSessionLocal)


async def _get(order_id: str):
    async with TestAsyncSessionLocal() as s:
        return await TradeResultRepository(s).get_by_order_id(order_id)


async def test_sell_pending_record_persists_exit_reason(patch_session):
    pid = await decision_maker._create_pending_record(
        symbol="005930", side="SELL", order_id="ORD-DAYCLOSE",
        quantity=10, expected_price=70000, exit_reason="DAY_CLOSE",
    )
    assert pid
    tr = await _get("ORD-DAYCLOSE")
    assert tr is not None
    # 핵심: 생성 시점에 실제 사유가 저장됨 (수정 전엔 "")
    assert tr.exit_reason == "DAY_CLOSE"
    # 복구 경로가 보는 값이 비어있지 않으므로 RECOVERED로 덮이지 않음
    assert (tr.exit_reason or "RECOVERED") == "DAY_CLOSE"


async def test_sell_pending_record_empty_falls_back_to_recovered(patch_session):
    # exit_reason 미전달 시 빈값 → 복구 경로에서 RECOVERED로 대체되는 기존 동작 확인
    pid = await decision_maker._create_pending_record(
        symbol="005930", side="SELL", order_id="ORD-EMPTY",
        quantity=10, expected_price=70000,
    )
    assert pid
    tr = await _get("ORD-EMPTY")
    assert tr.exit_reason == ""
    assert (tr.exit_reason or "RECOVERED") == "RECOVERED"
