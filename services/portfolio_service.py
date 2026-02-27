from loguru import logger

from exceptions.common import ServiceException
from models.portfolio import Portfolio, PortfolioHolding
from repositories.portfolio_repository import PortfolioRepository, PortfolioHoldingRepository
from schemas.portfolio_schema import PortfolioCreate, PortfolioUpdate


class PortfolioService:
    def __init__(
        self,
        portfolio_repo: PortfolioRepository,
        holding_repo: PortfolioHoldingRepository,
    ):
        self.portfolio_repo = portfolio_repo
        self.holding_repo = holding_repo

    async def get_all(self) -> list[Portfolio]:
        return await self.portfolio_repo.get_active_portfolios()

    async def get_by_id(self, portfolio_id: str) -> Portfolio:
        portfolio = await self.portfolio_repo.get_by_id(portfolio_id)
        if not portfolio:
            raise ServiceException.not_found(f"포트폴리오를 찾을 수 없습니다: {portfolio_id}")
        return portfolio

    async def get_with_holdings(self, portfolio_id: str) -> Portfolio:
        portfolio = await self.portfolio_repo.get_with_holdings(portfolio_id)
        if not portfolio:
            raise ServiceException.not_found(f"포트폴리오를 찾을 수 없습니다: {portfolio_id}")
        return portfolio

    async def create(self, data: PortfolioCreate) -> Portfolio:
        portfolio = Portfolio(
            name=data.name,
            type=data.type.value,
            mode=data.mode.value,
            budget=data.budget,
            cash=data.budget,
        )
        created = await self.portfolio_repo.create(portfolio)
        logger.info("포트폴리오 생성: {} ({})", data.name, data.type.value)
        return created

    async def update(self, portfolio_id: str, data: PortfolioUpdate) -> Portfolio:
        portfolio = await self.get_by_id(portfolio_id)
        if data.name is not None:
            portfolio.name = data.name
        if data.budget is not None:
            diff = data.budget - portfolio.budget
            portfolio.budget = data.budget
            portfolio.cash += diff
        if data.is_active is not None:
            portfolio.is_active = data.is_active
        return await self.portfolio_repo.update(portfolio)

    async def get_holdings(self, portfolio_id: str) -> list[PortfolioHolding]:
        await self.get_by_id(portfolio_id)
        return await self.holding_repo.get_by_portfolio(portfolio_id)

    async def get_performance(self, portfolio_id: str) -> dict:
        portfolio = await self.get_with_holdings(portfolio_id)
        stock_value = sum(
            h.quantity * h.current_price for h in portfolio.holdings
        )
        total_asset = portfolio.cash + stock_value
        total_pnl = total_asset - portfolio.budget
        total_pnl_rate = (total_pnl / portfolio.budget * 100) if portfolio.budget > 0 else 0.0
        return {
            "portfolio_id": portfolio_id,
            "total_asset": total_asset,
            "cash": portfolio.cash,
            "stock_value": stock_value,
            "total_pnl": total_pnl,
            "total_pnl_rate": total_pnl_rate,
            "total_trades": 0,
            "win_rate": 0.0,
        }
