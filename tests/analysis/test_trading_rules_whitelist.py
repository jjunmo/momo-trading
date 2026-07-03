"""트레이딩 규칙 화이트리스트 테스트 — 청산 파라미터 거부 + 안전범위 클램프"""
from datetime import date

import pytest
from sqlalchemy import select

from models import TradingRule
from tests.conftest import TestAsyncSessionLocal


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    import analysis.feedback.trading_rules as tr_mod
    monkeypatch.setattr(tr_mod, "AsyncSessionLocal", TestAsyncSessionLocal)


@pytest.fixture()
async def clean_rules():
    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            for row in (await session.execute(select(TradingRule))).scalars().all():
                await session.delete(row)
    yield


class TestExitParamRejection:
    async def test_stop_loss_and_take_profit_rejected(self, clean_rules):
        """손절/익절 파라미터는 코드 소유 — LLM 제안 거부 (whipsaw 방지)"""
        from analysis.feedback.trading_rules import trading_rule_engine

        rules = await trading_rule_engine.generate_rules_from_review(
            {"action_items": [
                {"param_name": "stop_loss_pct", "param_value": -1.0, "reason": "촘촘한 손절"},
                {"param_name": "take_profit_pct", "param_value": 2.0, "reason": "빠른 익절"},
            ]},
            report_date=date.today(),
        )
        assert rules == []

    async def test_min_confidence_clamped_to_062(self, clean_rules):
        """min_confidence 상한 0.62 클램프 (매수 붕괴 회귀 방지)"""
        from analysis.feedback.trading_rules import trading_rule_engine

        rules = await trading_rule_engine.generate_rules_from_review(
            {"action_items": [
                {"param_name": "min_confidence", "param_value": 0.75,
                 "reason": "저신뢰 차단", "expected_effect": {"metric": "win_rate", "direction": "UP"}},
            ]},
            report_date=date.today(),
        )
        assert len(rules) == 1
        assert rules[0].param_value == 0.62
        assert "win_rate" in rules[0].expected_effect

    async def test_entry_control_param_allowed(self, clean_rules):
        """진입 제어 파라미터(rr_floor)는 정상 생성"""
        from analysis.feedback.trading_rules import trading_rule_engine

        rules = await trading_rule_engine.generate_rules_from_review(
            {"action_items": [
                {"param_name": "rr_floor", "param_value": 1.5, "reason": "RR 낮은 진입 차단"},
            ]},
            report_date=date.today(),
        )
        assert len(rules) == 1
        assert rules[0].param_value == 1.5
