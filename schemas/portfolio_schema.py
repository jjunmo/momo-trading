from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from trading.enums import PortfolioType, TradingMode


class PortfolioCreate(BaseModel):
    name: str
    type: PortfolioType
    mode: TradingMode = TradingMode.PAPER
    budget: float = 0.0


class PortfolioUpdate(BaseModel):
    name: Optional[str] = None
    budget: Optional[float] = None
    is_active: Optional[bool] = None


class HoldingResponse(BaseModel):
    id: str
    stock_id: str
    symbol: str = ""
    name: str = ""
    quantity: int
    avg_buy_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_rate: float

    model_config = {"from_attributes": True}


class PortfolioResponse(BaseModel):
    id: str
    name: str
    type: str
    mode: str
    budget: float
    cash: float
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PortfolioDetailResponse(PortfolioResponse):
    holdings: list[HoldingResponse] = []
    total_asset: float = 0.0
    total_stock_value: float = 0.0
    total_pnl: float = 0.0
    total_pnl_rate: float = 0.0


class PerformanceResponse(BaseModel):
    portfolio_id: str
    total_asset: float
    cash: float
    stock_value: float
    total_pnl: float
    total_pnl_rate: float
    total_trades: int
    win_rate: float = 0.0
