from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from trading.enums import Market


class StockCreate(BaseModel):
    symbol: str
    name: str
    market: Market
    category: Optional[str] = None
    exchange_code: Optional[str] = None


class StockUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class StockResponse(BaseModel):
    id: str
    symbol: str
    name: str
    market: str
    category: Optional[str] = None
    exchange_code: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StockSnapshotResponse(BaseModel):
    stock: StockResponse
    current_price: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    per: Optional[float] = None
    pbr: Optional[float] = None
