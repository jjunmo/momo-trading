"""Repository DI 등록"""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_async_db, get_async_db_with_transaction
from repositories.stock_repository import StockRepository
from repositories.portfolio_repository import PortfolioRepository, PortfolioHoldingRepository
from repositories.order_repository import OrderRepository
from repositories.market_data_repository import MarketDataDailyRepository, MarketSnapshotRepository
from repositories.analysis_repository import AnalysisRepository
from repositories.strategy_repository import StrategyConfigRepository, StrategySignalRepository
from repositories.recommendation_repository import RecommendationRepository
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository


# === Stock ===
async def get_stock_repo(db: AsyncSession = Depends(get_async_db)) -> StockRepository:
    return StockRepository(db)

async def get_stock_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> StockRepository:
    return StockRepository(db)

StockRepoDep = Annotated[StockRepository, Depends(get_stock_repo)]
StockRepoTxDep = Annotated[StockRepository, Depends(get_stock_repo_tx)]


# === Portfolio ===
async def get_portfolio_repo(db: AsyncSession = Depends(get_async_db)) -> PortfolioRepository:
    return PortfolioRepository(db)

async def get_portfolio_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> PortfolioRepository:
    return PortfolioRepository(db)

PortfolioRepoDep = Annotated[PortfolioRepository, Depends(get_portfolio_repo)]
PortfolioRepoTxDep = Annotated[PortfolioRepository, Depends(get_portfolio_repo_tx)]


# === PortfolioHolding ===
async def get_holding_repo(db: AsyncSession = Depends(get_async_db)) -> PortfolioHoldingRepository:
    return PortfolioHoldingRepository(db)

async def get_holding_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> PortfolioHoldingRepository:
    return PortfolioHoldingRepository(db)

HoldingRepoDep = Annotated[PortfolioHoldingRepository, Depends(get_holding_repo)]
HoldingRepoTxDep = Annotated[PortfolioHoldingRepository, Depends(get_holding_repo_tx)]


# === Order ===
async def get_order_repo(db: AsyncSession = Depends(get_async_db)) -> OrderRepository:
    return OrderRepository(db)

async def get_order_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> OrderRepository:
    return OrderRepository(db)

OrderRepoDep = Annotated[OrderRepository, Depends(get_order_repo)]
OrderRepoTxDep = Annotated[OrderRepository, Depends(get_order_repo_tx)]


# === MarketDataDaily ===
async def get_daily_repo(db: AsyncSession = Depends(get_async_db)) -> MarketDataDailyRepository:
    return MarketDataDailyRepository(db)

async def get_daily_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> MarketDataDailyRepository:
    return MarketDataDailyRepository(db)

DailyRepoDep = Annotated[MarketDataDailyRepository, Depends(get_daily_repo)]
DailyRepoTxDep = Annotated[MarketDataDailyRepository, Depends(get_daily_repo_tx)]


# === MarketSnapshot ===
async def get_snapshot_repo(db: AsyncSession = Depends(get_async_db)) -> MarketSnapshotRepository:
    return MarketSnapshotRepository(db)

async def get_snapshot_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> MarketSnapshotRepository:
    return MarketSnapshotRepository(db)

SnapshotRepoDep = Annotated[MarketSnapshotRepository, Depends(get_snapshot_repo)]
SnapshotRepoTxDep = Annotated[MarketSnapshotRepository, Depends(get_snapshot_repo_tx)]


# === Analysis ===
async def get_analysis_repo(db: AsyncSession = Depends(get_async_db)) -> AnalysisRepository:
    return AnalysisRepository(db)

async def get_analysis_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> AnalysisRepository:
    return AnalysisRepository(db)

AnalysisRepoDep = Annotated[AnalysisRepository, Depends(get_analysis_repo)]
AnalysisRepoTxDep = Annotated[AnalysisRepository, Depends(get_analysis_repo_tx)]


# === StrategyConfig ===
async def get_strategy_config_repo(db: AsyncSession = Depends(get_async_db)) -> StrategyConfigRepository:
    return StrategyConfigRepository(db)

async def get_strategy_config_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> StrategyConfigRepository:
    return StrategyConfigRepository(db)

StrategyConfigRepoDep = Annotated[StrategyConfigRepository, Depends(get_strategy_config_repo)]
StrategyConfigRepoTxDep = Annotated[StrategyConfigRepository, Depends(get_strategy_config_repo_tx)]


# === StrategySignal ===
async def get_strategy_signal_repo(db: AsyncSession = Depends(get_async_db)) -> StrategySignalRepository:
    return StrategySignalRepository(db)

async def get_strategy_signal_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> StrategySignalRepository:
    return StrategySignalRepository(db)

StrategySignalRepoDep = Annotated[StrategySignalRepository, Depends(get_strategy_signal_repo)]
StrategySignalRepoTxDep = Annotated[StrategySignalRepository, Depends(get_strategy_signal_repo_tx)]


# === Recommendation ===
async def get_recommendation_repo(db: AsyncSession = Depends(get_async_db)) -> RecommendationRepository:
    return RecommendationRepository(db)

async def get_recommendation_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> RecommendationRepository:
    return RecommendationRepository(db)

RecommendationRepoDep = Annotated[RecommendationRepository, Depends(get_recommendation_repo)]
RecommendationRepoTxDep = Annotated[RecommendationRepository, Depends(get_recommendation_repo_tx)]


# === AgentActivity ===
async def get_activity_repo(db: AsyncSession = Depends(get_async_db)) -> AgentActivityRepository:
    return AgentActivityRepository(db)

async def get_activity_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> AgentActivityRepository:
    return AgentActivityRepository(db)

ActivityRepoDep = Annotated[AgentActivityRepository, Depends(get_activity_repo)]
ActivityRepoTxDep = Annotated[AgentActivityRepository, Depends(get_activity_repo_tx)]


# === DailyReport ===
async def get_daily_report_repo(db: AsyncSession = Depends(get_async_db)) -> DailyReportRepository:
    return DailyReportRepository(db)

async def get_daily_report_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> DailyReportRepository:
    return DailyReportRepository(db)

DailyReportRepoDep = Annotated[DailyReportRepository, Depends(get_daily_report_repo)]
DailyReportRepoTxDep = Annotated[DailyReportRepository, Depends(get_daily_report_repo_tx)]
