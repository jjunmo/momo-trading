from fastapi import APIRouter

from api.routes import (
    health,
    stocks,
    portfolio,
    orders,
    analysis,
    strategy,
    recommendations,
    dashboard,
    backtest,
    feedback,
    admin,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(stocks.router)
api_router.include_router(portfolio.router)
api_router.include_router(orders.router)
api_router.include_router(analysis.router)
api_router.include_router(strategy.router)
api_router.include_router(recommendations.router)
api_router.include_router(dashboard.router)
api_router.include_router(backtest.router)
api_router.include_router(feedback.router)
api_router.include_router(admin.router)
