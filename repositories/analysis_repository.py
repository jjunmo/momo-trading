from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.analysis import AnalysisResult
from repositories.async_base_repository import AsyncBaseRepository


class AnalysisRepository(AsyncBaseRepository[AnalysisResult]):
    def __init__(self, db: AsyncSession):
        super().__init__(AnalysisResult, db)

    async def get_by_stock(
        self, stock_id: str, limit: int = 10
    ) -> list[AnalysisResult]:
        result = await self.db.execute(
            select(AnalysisResult)
            .where(AnalysisResult.stock_id == stock_id)
            .order_by(AnalysisResult.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest_by_stock(self, stock_id: str) -> AnalysisResult | None:
        result = await self.db.execute(
            select(AnalysisResult)
            .where(AnalysisResult.stock_id == stock_id)
            .order_by(AnalysisResult.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_recent(self, limit: int = 20) -> list[AnalysisResult]:
        result = await self.db.execute(
            select(AnalysisResult)
            .order_by(AnalysisResult.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
