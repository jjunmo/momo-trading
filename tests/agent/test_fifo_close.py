"""close_open_buys_fifo — 매수↔매도 FIFO 동기화 청산 검증.

- 단일 전량 청산: 매도가·사유·sell_order_id·손익 기록
- 복수 매수 FIFO + 부분 분할: 오래된 매수부터 소진, 부분 소진은 분할
- 매도수량 초과: 미청산 전량만 청산
"""
import datetime

import pytest
from sqlalchemy import select

from models.trade_result import TradeResult
from repositories.trade_result_repository import TradeResultRepository
from tests.conftest import TestAsyncSessionLocal
from util.time_util import KST

NOW = datetime.datetime(2026, 6, 22, 14, 0, tzinfo=KST)


def _buy(symbol, qty, price, hour, oid):
    return TradeResult(
        order_id=oid, stock_symbol=symbol, stock_name=symbol, side="BUY",
        strategy_type="STABLE_SHORT", entry_price=price, quantity=qty,
        market="KOSPI", market_regime="BULL", status="CONFIRMED",
        entry_at=NOW.replace(hour=hour),
    )


async def _open_buys(session, symbol):
    rows = (await session.execute(
        select(TradeResult).where(
            TradeResult.stock_symbol == symbol, TradeResult.side == "BUY",
            TradeResult.exit_at.is_(None),
        ).order_by(TradeResult.entry_at.asc())
    )).scalars().all()
    return list(rows)


async def test_full_close_single_buy():
    async with TestAsyncSessionLocal() as s:
        s.add(_buy("AAA", 10, 1000, 10, "AAA-B1"))
        await s.commit()
    async with TestAsyncSessionLocal() as s:
        repo = TradeResultRepository(s)
        total_pnl, closed = await repo.close_open_buys_fifo(
            "AAA", sell_qty=10, sell_price=1100, exit_reason="DAY_CLOSE",
            sell_order_id="AAA-S1", now=NOW,
        )
        await s.commit()
    assert closed == 10
    async with TestAsyncSessionLocal() as s:
        rows = (await s.execute(select(TradeResult).where(TradeResult.stock_symbol == "AAA"))).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.exit_at is not None
        assert r.exit_price == 1100
        assert r.exit_reason == "DAY_CLOSE"
        assert r.sell_order_id == "AAA-S1"
        assert r.pnl > 0


async def test_fifo_partial_split_across_buys():
    async with TestAsyncSessionLocal() as s:
        s.add(_buy("BBB", 10, 1000, 10, "BBB-B1"))  # 오래됨
        s.add(_buy("BBB", 5, 1000, 11, "BBB-B2"))   # 최신
        await s.commit()
    async with TestAsyncSessionLocal() as s:
        repo = TradeResultRepository(s)
        _, closed = await repo.close_open_buys_fifo(
            "BBB", sell_qty=12, sell_price=1100, exit_reason="STOP_LOSS",
            sell_order_id="BBB-S1", now=NOW,
        )
        await s.commit()
    assert closed == 12
    async with TestAsyncSessionLocal() as s:
        open_rows = await _open_buys(s, "BBB")
        # B1(10) 전량 청산, B2(5)는 2 청산 + 3 잔량 → 미청산 1행(3주)
        assert len(open_rows) == 1
        assert open_rows[0].quantity == 3
        all_closed = (await s.execute(
            select(TradeResult).where(
                TradeResult.stock_symbol == "BBB", TradeResult.exit_at.isnot(None))
        )).scalars().all()
        assert sum(r.quantity for r in all_closed) == 12  # 10 + 분할 2


async def test_sell_exceeds_open_qty():
    async with TestAsyncSessionLocal() as s:
        s.add(_buy("CCC", 10, 1000, 10, "CCC-B1"))
        await s.commit()
    async with TestAsyncSessionLocal() as s:
        repo = TradeResultRepository(s)
        _, closed = await repo.close_open_buys_fifo(
            "CCC", sell_qty=15, sell_price=1100, exit_reason="DAY_CLOSE",
            sell_order_id="CCC-S1", now=NOW,
        )
        await s.commit()
    assert closed == 10  # 미청산 10주만 (초과 5주는 매칭 불가 → 경고)


async def test_partial_close_keeps_runner_flag_on_open_row():
    """분할 익절(러너 전환 후) 부분 청산: 잔량 원행의 is_runner 유지, 청산 분할행은 기본값"""
    async with TestAsyncSessionLocal() as s:
        buy = _buy("DDD", 10, 1000, 10, "DDD-B1")
        buy.is_runner = True  # 러너 전환된 포지션
        s.add(buy)
        await s.commit()
    async with TestAsyncSessionLocal() as s:
        repo = TradeResultRepository(s)
        _, closed = await repo.close_open_buys_fifo(
            "DDD", sell_qty=5, sell_price=1100, exit_reason="TAKE_PROFIT_REVIEW",
            sell_order_id="DDD-S1", now=NOW,
        )
        await s.commit()
    assert closed == 5
    async with TestAsyncSessionLocal() as s:
        open_rows = await _open_buys(s, "DDD")
        assert len(open_rows) == 1
        assert open_rows[0].quantity == 5
        assert open_rows[0].is_runner is True  # 재시작 복원용 플래그 유지
        closed_rows = (await s.execute(
            select(TradeResult).where(
                TradeResult.stock_symbol == "DDD", TradeResult.exit_at.isnot(None))
        )).scalars().all()
        assert len(closed_rows) == 1
        assert closed_rows[0].quantity == 5
