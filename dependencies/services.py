"""Service DI 등록"""
from typing import Annotated

from fastapi import Depends

from dependencies.repositories import (
    StockRepoDep, StockRepoTxDep,
    DailyRepoDep, DailyRepoTxDep,
    SnapshotRepoDep, SnapshotRepoTxDep,
    AnalysisRepoDep, AnalysisRepoTxDep,
    StrategyConfigRepoDep, StrategyConfigRepoTxDep,
    StrategySignalRepoDep, StrategySignalRepoTxDep,
)
from services.stock_service import StockService
from services.market_data_service import MarketDataService
from services.analysis_service import AnalysisService
from services.strategy_service import StrategyService
from services.trading_service import TradingService


# === Stock ===
def get_stock_service(stock_repo: StockRepoDep) -> StockService:
    return StockService(stock_repo=stock_repo)

def get_stock_service_tx(stock_repo: StockRepoTxDep) -> StockService:
    return StockService(stock_repo=stock_repo)

StockServiceDep = Annotated[StockService, Depends(get_stock_service)]
StockServiceTxDep = Annotated[StockService, Depends(get_stock_service_tx)]


# === MarketData ===
def get_market_data_service(
    daily_repo: DailyRepoDep,
    snapshot_repo: SnapshotRepoDep,
) -> MarketDataService:
    return MarketDataService(daily_repo=daily_repo, snapshot_repo=snapshot_repo)

def get_market_data_service_tx(
    daily_repo: DailyRepoTxDep,
    snapshot_repo: SnapshotRepoTxDep,
) -> MarketDataService:
    return MarketDataService(daily_repo=daily_repo, snapshot_repo=snapshot_repo)

MarketDataServiceDep = Annotated[MarketDataService, Depends(get_market_data_service)]
MarketDataServiceTxDep = Annotated[MarketDataService, Depends(get_market_data_service_tx)]


# === Analysis ===
def get_analysis_service(analysis_repo: AnalysisRepoDep) -> AnalysisService:
    return AnalysisService(analysis_repo=analysis_repo)

def get_analysis_service_tx(analysis_repo: AnalysisRepoTxDep) -> AnalysisService:
    return AnalysisService(analysis_repo=analysis_repo)

AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
AnalysisServiceTxDep = Annotated[AnalysisService, Depends(get_analysis_service_tx)]


# === Strategy ===
def get_strategy_service(
    config_repo: StrategyConfigRepoDep,
    signal_repo: StrategySignalRepoDep,
) -> StrategyService:
    return StrategyService(config_repo=config_repo, signal_repo=signal_repo)

def get_strategy_service_tx(
    config_repo: StrategyConfigRepoTxDep,
    signal_repo: StrategySignalRepoTxDep,
) -> StrategyService:
    return StrategyService(config_repo=config_repo, signal_repo=signal_repo)

StrategyServiceDep = Annotated[StrategyService, Depends(get_strategy_service)]
StrategyServiceTxDep = Annotated[StrategyService, Depends(get_strategy_service_tx)]


# === Trading (MCP 기반, DI 불필요 - 싱글톤) ===
def get_trading_service() -> TradingService:
    return TradingService()

TradingServiceDep = Annotated[TradingService, Depends(get_trading_service)]
