"""급등 감지 즉시 재스캔 (_check_surge) 로직 검증.

- 등락률 임계값 + 거래량 동반 조건을 모두 만족하는 '신규' 급등만 재스캔 트리거
- 이미 본 급등은 재트리거 안 함
- 직전 스캔 후 쿨다운 중이면 보류
"""
import datetime
import time

import pytest

from agent.market_regime_agent import MarketRegimeAgent


@pytest.fixture
def patched(monkeypatch):
    # 장중 + 마감 전으로 고정
    from scheduler import market_calendar as mc_mod
    monkeypatch.setattr(mc_mod.market_calendar, "is_domestic_trading_hours", lambda *a, **k: True)
    monkeypatch.setattr(mc_mod.market_calendar, "get_trading_cutoff", lambda *a, **k: datetime.time(15, 0))
    monkeypatch.setattr("util.time_util.now_kst", lambda: datetime.datetime(2026, 6, 22, 10, 0))
    # activity_logger.log noop
    from services import activity_logger as al_mod
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(al_mod.activity_logger, "log", _noop)


def _set_surges(monkeypatch, data):
    from agent import market_scanner as ms_mod
    async def _fake(sort):
        return data
    monkeypatch.setattr(ms_mod.market_scanner, "_get_fluctuation_rank", _fake)


SURGE_DATA = [
    {"symbol": "AAA", "change_rate": "6.0", "volume_increase_rate": "250"},  # hot
    {"symbol": "BBB", "change_rate": "6.0", "volume_increase_rate": "100"},  # 거래량 미달 → 제외
    {"symbol": "CCC", "change_rate": "3.0", "volume_increase_rate": "300"},  # 등락률 미달 → 제외
]


async def test_new_surge_triggers_rescan(patched, monkeypatch):
    _set_surges(monkeypatch, SURGE_DATA)
    agent = MarketRegimeAgent()
    agent._last_scan_at = 0.0  # 쿨다운 지난 상태
    calls = []
    async def cb():
        calls.append(1)
    agent.set_scan_trigger_callback(cb)

    await agent._check_surge()
    assert calls == [1]                 # 거래량 동반 신규 급등 1건(AAA) → 트리거
    assert agent._surge_seen == {"AAA"} # BBB/CCC는 제외


async def test_already_seen_surge_not_retriggered(patched, monkeypatch):
    _set_surges(monkeypatch, SURGE_DATA)
    agent = MarketRegimeAgent()
    agent._last_scan_at = 0.0
    calls = []
    async def cb():
        calls.append(1)
    agent.set_scan_trigger_callback(cb)

    await agent._check_surge()  # 1차: AAA 트리거
    await agent._check_surge()  # 2차: 동일 데이터 → 신규 없음
    assert calls == [1]


async def test_cooldown_blocks_rescan(patched, monkeypatch):
    _set_surges(monkeypatch, SURGE_DATA)
    agent = MarketRegimeAgent()
    agent._last_scan_at = time.time()  # 방금 스캔함 → 쿨다운 중
    calls = []
    async def cb():
        calls.append(1)
    agent.set_scan_trigger_callback(cb)

    await agent._check_surge()
    assert calls == []  # 신규 급등 있어도 쿨다운으로 보류
