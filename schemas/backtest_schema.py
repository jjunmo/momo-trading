from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    """백테스팅 실행 요청"""
    symbol: str = Field(..., description="종목 심볼 (예: 005930)")
    strategy_type: str = Field("STABLE_SHORT", description="전략 유형: STABLE_SHORT / AGGRESSIVE_SHORT")
    start_date: Optional[date] = Field(None, description="시작일 (미지정 시 6개월 전)")
    end_date: Optional[date] = Field(None, description="종료일 (미지정 시 오늘)")
    initial_capital: float = Field(10_000_000, description="초기 자본 (원)")
    max_position_pct: float = Field(20.0, description="종목당 최대 비중 (%)")
    commission_rate: float = Field(0.015, description="수수료율 (%)")
    slippage_rate: float = Field(0.05, description="슬리피지 (%)")
    stop_loss_pct: float = Field(-3.0, description="손절 (%)")
    take_profit_pct: float = Field(5.0, description="익절 (%)")
    max_hold_days: int = Field(5, description="최대 보유 일수")


class TradeRecordResponse(BaseModel):
    """개별 거래 기록"""
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    return_pct: float
    hold_days: int
    reason: str


class BacktestMetricsResponse(BaseModel):
    """백테스팅 성과 지표"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_return_pct: float
    total_pnl: float
    avg_return_per_trade: float
    max_single_win_pct: float
    max_single_loss_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    profit_factor: float | str
    avg_hold_days: float
    avg_win_return_pct: float
    avg_loss_return_pct: float
    initial_capital: float
    final_capital: float


class BacktestResultResponse(BaseModel):
    """백테스팅 결과"""
    symbol: str
    strategy_type: str
    config: dict
    summary: str
    metrics: BacktestMetricsResponse
    trade_summary: dict
    trades: list[TradeRecordResponse]
