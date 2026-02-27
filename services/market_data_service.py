from datetime import date

from models.market_data import MarketDataDaily, MarketSnapshot
from repositories.market_data_repository import MarketDataDailyRepository, MarketSnapshotRepository


class MarketDataService:
    def __init__(
        self,
        daily_repo: MarketDataDailyRepository,
        snapshot_repo: MarketSnapshotRepository,
    ):
        self.daily_repo = daily_repo
        self.snapshot_repo = snapshot_repo

    async def get_daily_data(
        self, stock_id: str, start_date: date, end_date: date
    ) -> list[MarketDataDaily]:
        return await self.daily_repo.get_by_stock_date_range(stock_id, start_date, end_date)

    async def get_latest_daily(self, stock_id: str, count: int = 30) -> list[MarketDataDaily]:
        return await self.daily_repo.get_latest(stock_id, count)

    async def save_daily_data(self, data: MarketDataDaily) -> MarketDataDaily:
        return await self.daily_repo.create(data)

    async def get_snapshot(self, stock_id: str) -> MarketSnapshot | None:
        return await self.snapshot_repo.get_by_stock(stock_id)

    async def upsert_snapshot(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        return await self.snapshot_repo.upsert(snapshot)
