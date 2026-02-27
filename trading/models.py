"""거래 관련 Pydantic 모델 (API 통신용, DB 모델 아님)"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from trading.enums import Market, OrderSide, OrderType


class KISTokenInfo(BaseModel):
    """KIS API 토큰 정보"""
    access_token: str
    token_type: str
    expires_at: datetime


class MCPRequest(BaseModel):
    """MCP 서버 요청"""
    tool_name: str
    arguments: dict


class MCPResponse(BaseModel):
    """MCP 서버 응답"""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class CurrentPrice(BaseModel):
    """현재가 정보"""
    symbol: str
    market: Market
    price: float
    change: float
    change_rate: float
    volume: int
    timestamp: datetime


class OrderRequest(BaseModel):
    """주문 요청"""
    symbol: str
    market: Market
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[float] = None  # 시장가 주문 시 None


class OrderResult(BaseModel):
    """주문 실행 결과"""
    success: bool
    order_id: Optional[str] = None
    message: str
    filled_quantity: int = 0
    filled_price: float = 0.0


class AccountBalance(BaseModel):
    """계좌 잔고"""
    total_asset: float
    cash: float
    stock_value: float
    total_pnl: float
    total_pnl_rate: float
    is_valid: bool = True  # False이면 조회 실패 상태


class HoldingInfo(BaseModel):
    """보유 종목 정보"""
    symbol: str
    name: str
    quantity: int
    avg_buy_price: float
    current_price: float
    pnl: float
    pnl_rate: float
