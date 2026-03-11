"""매매 결과 리포지토리"""
from datetime import date, datetime, time

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade_result import TradeResult
from repositories.async_base_repository import AsyncBaseRepository


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
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_strategy(self, strategy_type: str, limit: int = 100) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.strategy_type == strategy_type)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 50) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_open_buy(self, symbol: str) -> TradeResult | None:
        """미청산 매수 기록 조회 (exit_at IS NULL, side=BUY)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.stock_symbol == symbol,
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
            ))
            .order_by(TradeResult.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_completed_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜에 청산 완료된 거래 (exit_at 기준)"""
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
            ))
            .order_by(TradeResult.exit_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_opened_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜에 진입한 매수 기록 (entry_at 기준)"""
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.entry_at >= start,
                TradeResult.entry_at <= end,
            ))
            .order_by(TradeResult.entry_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_open(self) -> list[TradeResult]:
        """미청산 포지션 전체 조회 (exit_at IS NULL, side=BUY)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
            ))
            .order_by(TradeResult.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
