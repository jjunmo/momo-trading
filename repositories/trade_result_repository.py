"""매매 결과 리포지토리"""
from sqlalchemy import select, and_
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
