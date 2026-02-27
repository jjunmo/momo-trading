from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.strategy import StrategyConfig, StrategySignal
from repositories.async_base_repository import AsyncBaseRepository


class StrategyConfigRepository(AsyncBaseRepository[StrategyConfig]):
    def __init__(self, db: AsyncSession):
        super().__init__(StrategyConfig, db)

    async def get_by_type(self, strategy_type: str) -> Optional[StrategyConfig]:
        return await self.filter_by_one(type=strategy_type)

    async def get_active_strategies(self) -> list[StrategyConfig]:
        return await self.filter_by(is_active=True)


class StrategySignalRepository(AsyncBaseRepository[StrategySignal]):
    def __init__(self, db: AsyncSession):
        super().__init__(StrategySignal, db)

    async def get_by_stock(
        self, stock_id: str, limit: int = 10
    ) -> list[StrategySignal]:
        result = await self.db.execute(
            select(StrategySignal)
            .where(StrategySignal.stock_id == stock_id)
            .order_by(StrategySignal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent_signals(self, limit: int = 20) -> list[StrategySignal]:
        result = await self.db.execute(
            select(StrategySignal)
            .order_by(StrategySignal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_actionable_signals(self) -> list[StrategySignal]:
        """BUY/SELL 시그널만 조회 (HOLD 제외)"""
        result = await self.db.execute(
            select(StrategySignal)
            .where(StrategySignal.action.in_(["BUY", "SELL"]))
            .order_by(StrategySignal.created_at.desc())
        )
        return list(result.scalars().all())
