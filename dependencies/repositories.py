"""Repository DI 등록"""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_async_db, get_async_db_with_transaction
from repositories.stock_repository import StockRepository
from repositories.market_data_repository import MarketDataDailyRepository, MarketSnapshotRepository
from repositories.analysis_repository import AnalysisRepository
from repositories.strategy_repository import StrategyConfigRepository, StrategySignalRepository
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository


# === Stock ===
async def get_stock_repo(db: AsyncSession = Depends(get_async_db)) -> StockRepository:
    return StockRepository(db)

async def get_stock_repo_tx(db: AsyncSession = Depends(get_async_db_with_transaction)) -> StockRepository:
    return StockRepository(db)

StockRepoDep = Annotated[StockRepository, Depends(get_stock_repo)]
StockRepoTxDep = Annotated[StockRepository, Depends(get_stock_repo_tx)]


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
