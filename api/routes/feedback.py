"""AI 피드백 루프 API - 매매 성과 추적 + 전략 조정"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.feedback.strategy_tuner import StrategyTuner
from core.database import get_async_db
from models.trade_result import TradeResult
from repositories.trade_result_repository import TradeResultRepository
from schemas.common import SuccessResponse
from schemas.feedback_schema import (
    TradeResultCreate,
    TradeResultResponse,
    PerformanceStatResponse,
    StrategyTuningResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("/trade-results", response_model=SuccessResponse[list[TradeResultResponse]])
async def list_trade_results(
    session: Annotated[AsyncSession, Depends(get_async_db)],
    symbol: str | None = None,
    strategy_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    """매매 결과 목록 조회"""
    repo = TradeResultRepository(session)
    if symbol:
        results = await repo.get_by_symbol(symbol, limit)
    elif strategy_type:
        results = await repo.get_by_strategy(strategy_type, limit)
    else:
        results = await repo.get_recent(limit)
    return SuccessResponse(data=results)


@router.get("/stats/strategy/{strategy_type}", response_model=SuccessResponse[PerformanceStatResponse])
async def get_strategy_stats(
    strategy_type: str,
    session: Annotated[AsyncSession, Depends(get_async_db)],
):
    """전략별 성과 통계"""
    tracker = PerformanceTracker(session)
    stat = await tracker.get_strategy_stats(strategy_type)
    return SuccessResponse(data=PerformanceStatResponse(
        total_trades=stat.total_trades,
        wins=stat.wins,
        losses=stat.losses,
        win_rate=round(stat.win_rate * 100, 1),
        avg_return=round(stat.avg_return, 2),
        avg_pnl=round(stat.avg_pnl, 0),
        total_pnl=round(stat.total_pnl, 0),
        avg_hold_days=round(stat.avg_hold_days, 1),
        best_return=round(stat.best_return, 2),
        worst_return=round(stat.worst_return, 2),
    ))


@router.get("/stats/symbol/{symbol}", response_model=SuccessResponse[PerformanceStatResponse])
async def get_symbol_stats(
    symbol: str,
    session: Annotated[AsyncSession, Depends(get_async_db)],
):
    """종목별 성과 통계"""
    tracker = PerformanceTracker(session)
    stat = await tracker.get_symbol_stats(symbol)
    return SuccessResponse(data=PerformanceStatResponse(
        total_trades=stat.total_trades,
        wins=stat.wins,
        losses=stat.losses,
        win_rate=round(stat.win_rate * 100, 1),
        avg_return=round(stat.avg_return, 2),
        avg_pnl=round(stat.avg_pnl, 0),
        total_pnl=round(stat.total_pnl, 0),
        avg_hold_days=round(stat.avg_hold_days, 1),
        best_return=round(stat.best_return, 2),
        worst_return=round(stat.worst_return, 2),
    ))


@router.get("/stats/overall", response_model=SuccessResponse[dict])
async def get_overall_stats(
    session: Annotated[AsyncSession, Depends(get_async_db)],
):
    """전체 매매 성과 요약"""
    tracker = PerformanceTracker(session)
    stats = await tracker.get_overall_stats()

    # PerformanceStat → dict 변환
    def stat_to_dict(s):
        return {
            "total_trades": s.total_trades,
            "wins": s.wins,
            "losses": s.losses,
            "win_rate": round(s.win_rate * 100, 1),
            "avg_return": round(s.avg_return, 2),
            "total_pnl": round(s.total_pnl, 0),
        }

    result = {
        "overall": stat_to_dict(stats["overall"]),
        "by_strategy": {k: stat_to_dict(v) for k, v in stats["by_strategy"].items()},
        "total_records": stats["total_records"],
    }
    return SuccessResponse(data=result)


@router.get("/tuning/{strategy_type}", response_model=SuccessResponse[StrategyTuningResponse])
async def get_strategy_tuning(
    strategy_type: str,
    session: Annotated[AsyncSession, Depends(get_async_db)],
):
    """전략 파라미터 자동 조정 제안"""
    tuner = StrategyTuner(session)
    suggestions = await tuner.suggest_adjustments(strategy_type)
    return SuccessResponse(data=suggestions)
