"""피드백 관련 스키마"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TradeResultCreate(BaseModel):
    """매매 결과 기록 생성"""
    order_id: Optional[str] = None
    stock_symbol: str
    stock_name: str
    side: str
    strategy_type: str
    entry_price: float
    exit_price: float = 0.0
    quantity: int
    pnl: float = 0.0
    return_pct: float = 0.0
    hold_days: int = 0
    exit_reason: str = ""
    ai_recommendation: str = ""
    ai_confidence: float = 0.0
    ai_target_price: Optional[float] = None
    ai_stop_loss_price: Optional[float] = None
    entry_rsi: Optional[float] = None
    entry_macd_hist: Optional[float] = None
    entry_bb_position: Optional[str] = None
    entry_pattern: Optional[str] = None
    market: str = "KRX"
    market_regime: str = ""


class TradeResultResponse(BaseModel):
    """매매 결과 응답"""
    id: str
    stock_symbol: str
    stock_name: str
    side: str
    strategy_type: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    return_pct: float
    is_win: bool
    hold_days: int
    exit_reason: str
    ai_recommendation: str
    ai_confidence: float
    ai_target_price: Optional[float] = None
    ai_stop_loss_price: Optional[float] = None
    entry_rsi: Optional[float] = None
    entry_pattern: Optional[str] = None
    market_regime: str = ""
    status: str = "CONFIRMED"
    entry_at: Optional[datetime] = None
    exit_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PerformanceStatResponse(BaseModel):
    """성과 통계"""
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_return: float
    avg_pnl: float
    total_pnl: float
    avg_hold_days: float
    best_return: float
    worst_return: float


class StrategyTuningResponse(BaseModel):
    """전략 조정 제안"""
    status: str
    strategy_type: Optional[str] = None
    total_trades: Optional[int] = None
    current_win_rate: Optional[float] = None
    current_avg_return: Optional[float] = None
    adjustments: list[dict]
    message: Optional[str] = None
