from datetime import datetime

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.order import Order
from repositories.async_base_repository import AsyncBaseRepository


class OrderRepository(AsyncBaseRepository[Order]):
    def __init__(self, db: AsyncSession):
        super().__init__(Order, db)

    async def get_by_portfolio(
        self, portfolio_id: str, skip: int = 0, limit: int = 50
    ) -> list[Order]:
        result = await self.db.execute(
            select(Order)
            .where(Order.portfolio_id == portfolio_id)
            .order_by(Order.created_at.desc())
            .offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_status(self, status: str) -> list[Order]:
        return await self.filter_by(status=status)

    async def get_pending_orders(self) -> list[Order]:
        result = await self.db.execute(
            select(Order).where(Order.status.in_(["PENDING", "SUBMITTED"]))
        )
        return list(result.scalars().all())

    async def count_today_trades(self, portfolio_id: str, today: datetime) -> int:
        result = await self.db.execute(
            select(Order).where(
                and_(
                    Order.portfolio_id == portfolio_id,
                    Order.status == "FILLED",
                    Order.filled_at >= today,
                )
            )
        )
        return len(result.scalars().all())
