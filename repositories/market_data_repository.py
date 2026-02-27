from datetime import date
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.market_data import MarketDataDaily, MarketSnapshot
from repositories.async_base_repository import AsyncBaseRepository


class MarketDataDailyRepository(AsyncBaseRepository[MarketDataDaily]):
    def __init__(self, db: AsyncSession):
        super().__init__(MarketDataDaily, db)

    async def get_by_stock_date_range(
        self, stock_id: str, start_date: date, end_date: date
    ) -> list[MarketDataDaily]:
        result = await self.db.execute(
            select(MarketDataDaily)
            .where(
                and_(
                    MarketDataDaily.stock_id == stock_id,
                    MarketDataDaily.trade_date >= start_date,
                    MarketDataDaily.trade_date <= end_date,
                )
            )
            .order_by(MarketDataDaily.trade_date.asc())
        )
        return list(result.scalars().all())

    async def get_latest(self, stock_id: str, count: int = 30) -> list[MarketDataDaily]:
        result = await self.db.execute(
            select(MarketDataDaily)
            .where(MarketDataDaily.stock_id == stock_id)
            .order_by(MarketDataDaily.trade_date.desc())
            .limit(count)
        )
        return list(result.scalars().all())


class MarketSnapshotRepository(AsyncBaseRepository[MarketSnapshot]):
    def __init__(self, db: AsyncSession):
        super().__init__(MarketSnapshot, db)

    async def get_by_stock(self, stock_id: str) -> Optional[MarketSnapshot]:
        return await self.filter_by_one(stock_id=stock_id)

    async def upsert(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        existing = await self.get_by_stock(snapshot.stock_id)
        if existing:
            existing.current_price = snapshot.current_price
            existing.change = snapshot.change
            existing.change_rate = snapshot.change_rate
            existing.volume = snapshot.volume
            existing.high = snapshot.high
            existing.low = snapshot.low
            existing.open = snapshot.open
            existing.per = snapshot.per
            existing.pbr = snapshot.pbr
            existing.market_cap = snapshot.market_cap
            return await self.update(existing)
        return await self.create(snapshot)
