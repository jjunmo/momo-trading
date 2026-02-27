from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from trading.enums import Market, OrderSide, OrderType


class OrderCreate(BaseModel):
    portfolio_id: str
    symbol: str
    market: Market = Market.KOSPI
    side: OrderSide
    order_type: OrderType = OrderType.LIMIT
    quantity: int
    price: Optional[float] = None
    reason: Optional[str] = None


class OrderResponse(BaseModel):
    id: str
    portfolio_id: str
    stock_id: str
    side: str
    order_type: str
    status: str
    source: str
    quantity: int
    price: Optional[float]
    filled_quantity: int
    filled_price: float
    kis_order_id: Optional[str] = None
    reason: Optional[str] = None
    error_message: Optional[str] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
