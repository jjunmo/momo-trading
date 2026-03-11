"""일일 리포트 스키마"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class DailyReportResponse(BaseModel):
    id: str
    report_date: date
    total_cycles: int
    total_analyses: int
    total_recommendations: int
    total_orders: int
    buy_count: int = 0
    sell_count: int = 0
    win_count: int
    loss_count: int
    total_pnl: float
    unrealized_pnl: float = 0.0
    open_position_count: int = 0
    market_summary: Optional[str] = None
    performance_review: Optional[str] = None
    lessons_learned: Optional[str] = None
    next_day_plan: Optional[str] = None
    top_picks: Optional[str] = None
    strategy_stats: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
