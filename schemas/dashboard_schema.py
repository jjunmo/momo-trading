from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AssetSummary(BaseModel):
    total_asset: float
    total_cash: float
    total_stock_value: float
    total_pnl: float
    total_pnl_rate: float
    portfolio_count: int
    holding_count: int


class AgentActivity(BaseModel):
    id: str
    type: str  # SCAN, ANALYSIS, ORDER, RECOMMENDATION
    summary: str
    detail: Optional[str] = None
    timestamp: datetime


class RiskAlert(BaseModel):
    id: str
    level: str  # INFO, WARNING, CRITICAL
    message: str
    stock_id: Optional[str] = None
    symbol: Optional[str] = None
    timestamp: datetime


class SystemStatus(BaseModel):
    trading_enabled: bool
    autonomy_mode: str
    mcp_connected: bool
    websocket_connected: bool
    scheduler_running: bool
    active_subscriptions: int = 0
    last_agent_cycle: Optional[datetime] = None
