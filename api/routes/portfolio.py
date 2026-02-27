from fastapi import APIRouter

from dependencies.services import PortfolioServiceDep, PortfolioServiceTxDep
from schemas.common import SuccessResponse
from schemas.portfolio_schema import (
    HoldingResponse,
    PerformanceResponse,
    PortfolioCreate,
    PortfolioDetailResponse,
    PortfolioResponse,
    PortfolioUpdate,
)

router = APIRouter(prefix="/portfolios", tags=["포트폴리오"])


@router.get("", response_model=SuccessResponse[list[PortfolioResponse]])
async def get_portfolios(service: PortfolioServiceDep):
    portfolios = await service.get_all()
    return SuccessResponse(data=[PortfolioResponse.model_validate(p) for p in portfolios])


@router.post("", response_model=SuccessResponse[PortfolioResponse], status_code=201)
async def create_portfolio(data: PortfolioCreate, service: PortfolioServiceTxDep):
    portfolio = await service.create(data)
    return SuccessResponse(data=PortfolioResponse.model_validate(portfolio), message="포트폴리오가 생성되었습니다")


@router.get("/{portfolio_id}", response_model=SuccessResponse[PortfolioDetailResponse])
async def get_portfolio(portfolio_id: str, service: PortfolioServiceDep):
    portfolio = await service.get_with_holdings(portfolio_id)
    holdings = []
    stock_value = 0.0
    for h in portfolio.holdings:
        sv = h.quantity * h.current_price
        stock_value += sv
        holdings.append(HoldingResponse(
            id=h.id,
            stock_id=h.stock_id,
            quantity=h.quantity,
            avg_buy_price=h.avg_buy_price,
            current_price=h.current_price,
            unrealized_pnl=h.unrealized_pnl,
            unrealized_pnl_rate=h.unrealized_pnl_rate,
        ))
    total_asset = portfolio.cash + stock_value
    total_pnl = total_asset - portfolio.budget
    total_pnl_rate = (total_pnl / portfolio.budget * 100) if portfolio.budget > 0 else 0.0
    detail = PortfolioDetailResponse(
        id=portfolio.id,
        name=portfolio.name,
        type=portfolio.type,
        mode=portfolio.mode,
        budget=portfolio.budget,
        cash=portfolio.cash,
        is_active=portfolio.is_active,
        created_at=portfolio.created_at,
        holdings=holdings,
        total_asset=total_asset,
        total_stock_value=stock_value,
        total_pnl=total_pnl,
        total_pnl_rate=total_pnl_rate,
    )
    return SuccessResponse(data=detail)


@router.put("/{portfolio_id}", response_model=SuccessResponse[PortfolioResponse])
async def update_portfolio(portfolio_id: str, data: PortfolioUpdate, service: PortfolioServiceTxDep):
    portfolio = await service.update(portfolio_id, data)
    return SuccessResponse(data=PortfolioResponse.model_validate(portfolio))


@router.get("/{portfolio_id}/holdings", response_model=SuccessResponse[list[HoldingResponse]])
async def get_holdings(portfolio_id: str, service: PortfolioServiceDep):
    holdings = await service.get_holdings(portfolio_id)
    return SuccessResponse(data=[HoldingResponse(
        id=h.id,
        stock_id=h.stock_id,
        quantity=h.quantity,
        avg_buy_price=h.avg_buy_price,
        current_price=h.current_price,
        unrealized_pnl=h.unrealized_pnl,
        unrealized_pnl_rate=h.unrealized_pnl_rate,
    ) for h in holdings])


@router.get("/{portfolio_id}/performance", response_model=SuccessResponse[PerformanceResponse])
async def get_performance(portfolio_id: str, service: PortfolioServiceDep):
    performance = await service.get_performance(portfolio_id)
    return SuccessResponse(data=PerformanceResponse(**performance))
