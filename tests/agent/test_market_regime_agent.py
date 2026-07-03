"""국면 히스테리시스(2회 연속 확인) + 시초가 가드 검증"""
from datetime import datetime
from unittest.mock import patch

import pytest

from agent.market_regime_agent import MarketRegimeAgent


def _agent(initial: str = "BEAR") -> MarketRegimeAgent:
    agent = MarketRegimeAgent()
    agent._current_regime = initial
    return agent


async def _check_with(agent: MarketRegimeAgent, classified: tuple[str, bool]) -> str:
    """check_regime 1회 실행 — 지수 조회/캐시/시각을 모두 모킹"""
    kst_day = datetime(2026, 6, 11, 10, 0, 0)  # 09:10 이후
    with (
        patch("trading.kis_api.get_market_index", return_value={"success": True}),
        patch("util.time_util.now_kst", return_value=kst_day),
        patch.object(agent, "_ensure_daily_cache", return_value=None),
        patch.object(agent, "_classify_regime", return_value=classified),
        patch("scheduler.market_calendar.market_calendar.is_domestic_trading_hours",
              return_value=True),
        patch("services.activity_logger.activity_logger.log", return_value=None),
    ):
        return await agent.check_regime()


class TestHysteresis:
    async def test_flapping_sequence_transitions_once(self):
        """오늘 재현된 flapping 시퀀스 — 단발 판정은 전부 보류, 2연속만 전환"""
        agent = _agent("BEAR")
        seq = [("SIDEWAYS", False), ("BEAR", False), ("SIDEWAYS", False),
               ("BEAR", False), ("SIDEWAYS", False), ("SIDEWAYS", False)]
        regimes = [await _check_with(agent, c) for c in seq]

        # 단발 SIDEWAYS/BEAR 판정으로는 전환 없음, 마지막 2연속 SIDEWAYS에서만 전환
        assert regimes[:5] == ["BEAR"] * 5
        assert regimes[5] == "SIDEWAYS"

    async def test_override_transitions_immediately(self):
        """급변 오버라이드(±1.2%)는 확인 없이 즉시 전환"""
        agent = _agent("SIDEWAYS")
        assert await _check_with(agent, ("BEAR", True)) == "BEAR"

    async def test_initial_regime_set_immediately(self):
        """하루 시작(국면 미설정) — 첫 판정 즉시 적용"""
        agent = _agent("")
        assert await _check_with(agent, ("BULL", False)) == "BULL"

    async def test_same_regime_resets_pending(self):
        """현 국면 재확인이 끼면 전환 카운터 리셋"""
        agent = _agent("BEAR")
        await _check_with(agent, ("BULL", False))   # 1/2 보류
        await _check_with(agent, ("BEAR", False))   # 재확인 → 리셋
        result = await _check_with(agent, ("BULL", False))  # 다시 1/2 보류
        assert result == "BEAR"

    async def test_set_regime_votes_shared_with_check(self):
        """외부(스캔) 판정 + 모니터 판정 합산 2표로 전환"""
        agent = _agent("BEAR")
        with patch("services.activity_logger.activity_logger.log", return_value=None):
            agent.set_regime("BULL")            # 1/2 보류
        assert agent.current_regime == "BEAR"
        assert await _check_with(agent, ("BULL", False)) == "BULL"  # 2/2 전환

    async def test_set_regime_initial_immediate(self):
        """외부 최초 설정은 즉시 적용 (하루 시작 국면 공백 방지)"""
        agent = _agent("")
        agent.set_regime("BEAR")
        assert agent.current_regime == "BEAR"


class TestOpeningGuard:
    @pytest.mark.parametrize("hh,mm", [(9, 0), (9, 5), (8, 30)])
    async def test_before_0910_skips_check(self, hh, mm):
        """09:10 이전 — 지수 조회 없이 현 국면 유지"""
        agent = _agent("SIDEWAYS")
        kst = datetime(2026, 6, 11, hh, mm, 0)
        with (
            patch("util.time_util.now_kst", return_value=kst),
            patch("scheduler.market_calendar.market_calendar.is_domestic_trading_hours",
                  return_value=True),
            patch.object(agent, "_classify_regime") as mock_classify,
        ):
            result = await agent.check_regime()
        assert result == "SIDEWAYS"
        mock_classify.assert_not_called()
