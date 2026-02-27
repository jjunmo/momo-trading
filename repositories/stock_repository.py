from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.stock import Stock
from repositories.async_base_repository import AsyncBaseRepository


class StockRepository(AsyncBaseRepository[Stock]):
    def __init__(self, db: AsyncSession):
        super().__init__(Stock, db)

    async def get_by_symbol(self, symbol: str) -> Optional[Stock]:
        return await self.filter_by_one(symbol=symbol)

    async def get_by_market(self, market: str) -> list[Stock]:
        return await self.filter_by(market=market, is_active=True)

    async def get_active_stocks(self) -> list[Stock]:
        return await self.filter_by(is_active=True)

    async def search_by_name(self, name: str) -> list[Stock]:
        result = await self.db.execute(
            select(Stock).where(Stock.name.contains(name), Stock.is_active == True)
        )
        return list(result.scalars().all())
