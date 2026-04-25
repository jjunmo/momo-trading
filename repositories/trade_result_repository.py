"""매매 결과 리포지토리"""
from datetime import date, datetime, time

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade_result import TradeResult
from repositories.async_base_repository import AsyncBaseRepository
from util.time_util import KST


class TradeResultRepository(AsyncBaseRepository[TradeResult]):

    def __init__(self, session: AsyncSession):
        super().__init__(TradeResult, session)

    async def get_by_symbol(self, symbol: str, limit: int = 50) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.stock_symbol == symbol)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_strategy(self, strategy_type: str, limit: int = 100) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.strategy_type == strategy_type)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 50) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_open_buy(self, symbol: str) -> TradeResult | None:
        """미청산 매수 기록 조회 (exit_at IS NULL, side=BUY, status=CONFIRMED)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.stock_symbol == symbol,
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_open_buys(self, symbol: str) -> list[TradeResult]:
        """특정 종목의 미청산 BUY 전체 조회 (SELL 시 일괄 청산용)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.stock_symbol == symbol,
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.entry_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_completed_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜에 청산 완료된 포지션 (BUY→청산 기록만, CONFIRMED)

        SELL 레코드가 아닌, 청산된 BUY 레코드를 반환.
        이 레코드에 pnl, return_pct, is_win이 정확히 기록되어 있음.
        """
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.exit_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_sell_count_by_date(self, target_date: date) -> int:
        """특정 날짜의 매도 주문 건수 (SELL 레코드 수, CONFIRMED)"""
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(func.count())
            .select_from(TradeResult)
            .where(and_(
                TradeResult.side == "SELL",
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def get_sells_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜 SELL 레코드 전체 (BUY-SELL 매칭 여부 무관, CONFIRMED만)

        리포트 누락 가시화용 — `get_completed_by_date`가 status=CONFIRMED인 BUY만
        잡아 BUY가 CONFIRM_FAILED인 경우(폴링 race) 매도가 리포트에서 누락됐다.
        SELL 레코드 직접 조회로 매도된 종목을 빠짐없이 가시화한다.
        """
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "SELL",
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.exit_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_opened_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜에 진입한 매수 기록 (entry_at 기준, CONFIRMED만)"""
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.entry_at >= start,
                TradeResult.entry_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.entry_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_open(self) -> list[TradeResult]:
        """미청산 포지션 전체 조회 (exit_at IS NULL, side=BUY, status=CONFIRMED)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_order_id(self, order_id: str) -> TradeResult | None:
        """주문번호로 TradeResult 조회"""
        if not order_id:
            return None
        stmt = (
            select(TradeResult)
            .where(TradeResult.order_id == order_id)
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_confirms(self) -> list[TradeResult]:
        """PENDING_CONFIRM 상태 레코드 조회 (복구용)"""
        stmt = (
            select(TradeResult)
            .where(TradeResult.status == "PENDING_CONFIRM")
            .order_by(TradeResult.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
