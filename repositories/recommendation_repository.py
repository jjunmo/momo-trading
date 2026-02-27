from datetime import datetime

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.recommendation import Recommendation
from repositories.async_base_repository import AsyncBaseRepository


class RecommendationRepository(AsyncBaseRepository[Recommendation]):
    def __init__(self, db: AsyncSession):
        super().__init__(Recommendation, db)

    async def get_pending(self) -> list[Recommendation]:
        result = await self.db.execute(
            select(Recommendation)
            .where(Recommendation.status == "PENDING")
            .order_by(Recommendation.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_expired(self, now: datetime) -> list[Recommendation]:
        result = await self.db.execute(
            select(Recommendation)
            .where(
                and_(
                    Recommendation.status == "PENDING",
                    Recommendation.expires_at <= now,
                )
            )
        )
        return list(result.scalars().all())

    async def get_by_status(self, status: str, limit: int = 50) -> list[Recommendation]:
        result = await self.db.execute(
            select(Recommendation)
            .where(Recommendation.status == status)
            .order_by(Recommendation.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 20) -> list[Recommendation]:
        result = await self.db.execute(
            select(Recommendation)
            .order_by(Recommendation.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
