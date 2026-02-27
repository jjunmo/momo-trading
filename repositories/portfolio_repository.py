from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.portfolio import Portfolio, PortfolioHolding
from repositories.async_base_repository import AsyncBaseRepository


class PortfolioRepository(AsyncBaseRepository[Portfolio]):
    def __init__(self, db: AsyncSession):
        super().__init__(Portfolio, db)

    async def get_with_holdings(self, portfolio_id: str) -> Optional[Portfolio]:
        result = await self.db.execute(
            select(Portfolio)
            .options(selectinload(Portfolio.holdings))
            .where(Portfolio.id == portfolio_id)
        )
        return result.scalars().first()

    async def get_active_portfolios(self) -> list[Portfolio]:
        return await self.filter_by(is_active=True)

    async def get_by_type(self, portfolio_type: str) -> list[Portfolio]:
        return await self.filter_by(type=portfolio_type, is_active=True)


class PortfolioHoldingRepository(AsyncBaseRepository[PortfolioHolding]):
    def __init__(self, db: AsyncSession):
        super().__init__(PortfolioHolding, db)

    async def get_by_portfolio(self, portfolio_id: str) -> list[PortfolioHolding]:
        return await self.filter_by(portfolio_id=portfolio_id)

    async def get_by_portfolio_and_stock(
        self, portfolio_id: str, stock_id: str
    ) -> Optional[PortfolioHolding]:
        return await self.filter_by_one(portfolio_id=portfolio_id, stock_id=stock_id)
