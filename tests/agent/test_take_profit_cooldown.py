"""익절 재분석 무한 반복 발화 방지 — 진행중 가드(레이스) + 쿨다운 검증."""
import asyncio

import pytest

from realtime.event_detector import PriceGuard


@pytest.fixture
def guard(monkeypatch):
    g = PriceGuard()
    g.set_thresholds("AAA", take_profit=1000.0)
    calls = []

    async def fake_request_review(symbol):
        # 실제 _request_review의 finally 효과만 재현 (의존성 없이): 쿨다운 설정 + 가드 해제
        calls.append(symbol)
        g._tp_review_cooldown_until[symbol] = 1e18  # 사실상 무한 쿨다운
        g._review_in_progress.discard(symbol)

    monkeypatch.setattr(g, "_request_review", fake_request_review)
    return g, calls


async def _hit(g, price=1100.0):
    th = g.get_thresholds("AAA")
    await g._check_stop_take("AAA", price, th)


async def test_race_only_one_trigger_before_task_runs(guard):
    g, calls = guard
    # 태스크 실행 전 연속 다회 틱 → 동기 가드로 1회만 트리거돼야 (레이스 방지)
    await _hit(g)
    await _hit(g)
    await _hit(g)
    await asyncio.sleep(0)  # create_task된 fake_request_review 실행
    assert len(calls) == 1


async def test_cooldown_blocks_repeat(guard):
    g, calls = guard
    await _hit(g)
    await asyncio.sleep(0)
    assert len(calls) == 1
    # 쿨다운 중 재히트 → 차단
    await _hit(g)
    await asyncio.sleep(0)
    assert len(calls) == 1


async def test_retrigger_after_cooldown(guard):
    g, calls = guard
    await _hit(g)
    await asyncio.sleep(0)
    assert len(calls) == 1
    # 쿨다운 만료 → 재트리거 허용
    g._tp_review_cooldown_until["AAA"] = 0.0
    await _hit(g)
    await asyncio.sleep(0)
    assert len(calls) == 2
