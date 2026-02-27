from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from trading.enums import PortfolioType


class StrategyConfigCreate(BaseModel):
    name: str
    type: PortfolioType
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_days: int
    max_position_pct: float
    min_confidence: float = 0.6
    description: Optional[str] = None


class StrategyConfigUpdate(BaseModel):
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_hold_days: Optional[int] = None
    max_position_pct: Optional[float] = None
    min_confidence: Optional[float] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class StrategyConfigResponse(BaseModel):
    id: str
    name: str
    type: str
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_days: int
    max_position_pct: float
    min_confidence: float
    description: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategySignalResponse(BaseModel):
    id: str
    stock_id: str
    strategy_id: str
    action: str
    strength: float
    suggested_price: Optional[float] = None
    suggested_quantity: Optional[int] = None
    urgency: str
    reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
