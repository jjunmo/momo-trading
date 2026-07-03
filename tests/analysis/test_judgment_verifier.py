"""판단 검증 엔진 테스트 — 예상 vs 실제 채점 로직"""
from datetime import date, datetime, timedelta

import pytest

from models import JudgmentVerification, MarketDataDaily, Stock, TradeResult
from tests.conftest import TestAsyncSessionLocal


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    """검증 엔진이 테스트 DB를 쓰도록 세션 팩토리 교체"""
    import core.database
    monkeypatch.setattr(core.database, "AsyncSessionLocal", TestAsyncSessionLocal)


@pytest.fixture()
async def clean_tables():
    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            for model in (JudgmentVerification, MarketDataDaily, TradeResult, Stock):
                for row in (await session.execute(
                    __import__("sqlalchemy").select(model)
                )).scalars().all():
                    await session.delete(row)
    yield


def _make_trade(**kwargs) -> TradeResult:
    now = datetime.now()
    base = dict(
        stock_symbol="000001", stock_name="테스트", side="BUY",
        strategy_type="AGGRESSIVE_SHORT", entry_price=10000.0, exit_price=10100.0,
        quantity=10, pnl=1000.0, return_pct=1.0, is_win=True,
        exit_reason="TRAILING_STOP", ai_recommendation="BUY", ai_confidence=0.6,
        ai_target_price=10500.0, ai_stop_loss_price=9700.0,
        status="CONFIRMED", entry_at=now - timedelta(hours=5), exit_at=now - timedelta(hours=1),
    )
    base.update(kwargs)
    return TradeResult(**base)


async def _add_candle(session, symbol: str, d: date, high: float, low: float, close: float):
    stock = (await session.execute(
        __import__("sqlalchemy").select(Stock).where(Stock.symbol == symbol)
    )).scalar_one_or_none()
    if stock is None:
        stock = Stock(symbol=symbol, name=symbol, market="KOSPI")
        session.add(stock)
        await session.flush()
    session.add(MarketDataDaily(
        stock_id=stock.id, trade_date=d,
        open=close, high=high, low=low, close=close, volume=1000,
    ))


class TestBuyJudgment:
    async def test_target_hit_via_candle_high(self, clean_tables):
        """청산가는 목표 미달이어도 보유 중 고가가 목표 터치 → TARGET_HIT"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                tr = _make_trade(exit_price=10100.0)  # 목표 10500 미달 청산
                session.add(tr)
                await session.flush()
                await _add_candle(session, "000001", tr.entry_at.date(),
                                  high=10600.0, low=9900.0, close=10100.0)

        count = await judgment_verifier._verify_buy_judgments(lookback_days=14)
        assert count == 1

        async with TestAsyncSessionLocal() as session:
            jv = (await session.execute(
                __import__("sqlalchemy").select(JudgmentVerification)
            )).scalars().one()
            assert jv.judgment_type == "BUY_ANALYSIS"
            assert jv.verdict == "TARGET_HIT"
            assert jv.score == 1.0

    async def test_intervened_loss_without_candle(self, clean_tables):
        """일봉 없음 + 목표/손절 미도달 손실 청산 → INTERVENED_LOSS"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(_make_trade(
                    exit_price=9900.0, pnl=-1000.0, return_pct=-1.0, is_win=False,
                ))

        count = await judgment_verifier._verify_buy_judgments(lookback_days=14)
        assert count == 1

        async with TestAsyncSessionLocal() as session:
            jv = (await session.execute(
                __import__("sqlalchemy").select(JudgmentVerification)
            )).scalars().one()
            assert jv.verdict == "INTERVENED_LOSS"

    async def test_stop_hit(self, clean_tables):
        """손절가 이하 청산 → STOP_HIT"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(_make_trade(
                    exit_price=9600.0, pnl=-4000.0, return_pct=-4.0, is_win=False,
                ))

        await judgment_verifier._verify_buy_judgments(lookback_days=14)

        async with TestAsyncSessionLocal() as session:
            jv = (await session.execute(
                __import__("sqlalchemy").select(JudgmentVerification)
            )).scalars().one()
            assert jv.verdict == "STOP_HIT"

    async def test_idempotent(self, clean_tables):
        """같은 판단은 두 번 채점하지 않음"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(_make_trade())

        assert await judgment_verifier._verify_buy_judgments(lookback_days=14) == 1
        assert await judgment_verifier._verify_buy_judgments(lookback_days=14) == 0


class TestPeakSellJudgment:
    async def test_wrong_when_price_rises_after_sell(self, clean_tables):
        """정점 매도 후 다음 종가가 더 높음 → WRONG (조기매도)"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        exit_at = datetime.now() - timedelta(days=2)
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(_make_trade(exit_reason="ANALYSIS_SELL", exit_at=exit_at,
                                        entry_at=exit_at - timedelta(hours=3)))
                await _add_candle(session, "000001",
                                  (exit_at + timedelta(days=1)).date(),
                                  high=10600.0, low=10200.0, close=10500.0)

        count = await judgment_verifier._verify_peak_sells(lookback_days=14)
        assert count == 1

        async with TestAsyncSessionLocal() as session:
            jv = (await session.execute(
                __import__("sqlalchemy").select(JudgmentVerification)
                .where(JudgmentVerification.judgment_type == "PEAK_SELL")
            )).scalars().one()
            assert jv.verdict == "WRONG"

    async def test_correct_when_price_drops(self, clean_tables):
        """정점 매도 후 다음 종가 하락 → CORRECT"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        exit_at = datetime.now() - timedelta(days=2)
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(_make_trade(exit_reason="ANALYSIS_SELL", exit_at=exit_at,
                                        entry_at=exit_at - timedelta(hours=3)))
                await _add_candle(session, "000001",
                                  (exit_at + timedelta(days=1)).date(),
                                  high=10100.0, low=9700.0, close=9800.0)

        await judgment_verifier._verify_peak_sells(lookback_days=14)

        async with TestAsyncSessionLocal() as session:
            jv = (await session.execute(
                __import__("sqlalchemy").select(JudgmentVerification)
                .where(JudgmentVerification.judgment_type == "PEAK_SELL")
            )).scalars().one()
            assert jv.verdict == "CORRECT"

    async def test_skipped_without_next_candle(self, clean_tables):
        """다음 거래일 일봉 없음 → 채점 보류 (다음 실행 대기)"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(_make_trade(exit_reason="ANALYSIS_SELL"))

        assert await judgment_verifier._verify_peak_sells(lookback_days=14) == 0


class TestAccuracyStats:
    async def test_stats_and_trust_gate(self, clean_tables):
        """적중률 집계 + 권한 가중 게이트 판정"""
        from analysis.feedback.judgment_verifier import judgment_verifier

        now = datetime.now()
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                for i in range(10):
                    session.add(JudgmentVerification(
                        judgment_type="PEAK_SELL",
                        verdict="WRONG" if i < 8 else "CORRECT",
                        judged_at=now, verified_at=now,
                    ))

        stats = await judgment_verifier.get_accuracy_stats()
        assert stats["PEAK_SELL"]["n"] == 10
        assert stats["PEAK_SELL"]["rate"] == 20.0

        # 적중률 20% < 50% + 표본 10건 → 신뢰 불가
        assert await judgment_verifier.is_judgment_trusted("PEAK_SELL") is False
        # 표본 없는 유형 → 기본 신뢰 (보수 게이트가 별도 방어)
        assert await judgment_verifier.is_judgment_trusted("MARKET_REGIME") is True
