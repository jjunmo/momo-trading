"""매매 총괄 서비스 - MCP를 통한 주문 실행 + 계좌 조회"""
from loguru import logger

from core.config import settings
from exceptions.common import ServiceException
from trading.account_manager import account_manager
from trading.mcp_client import mcp_client
from trading.models import AccountBalance, OrderRequest, OrderResult


class TradingService:
    """매매 총괄 서비스"""

    async def check_trading_enabled(self) -> None:
        if not settings.TRADING_ENABLED:
            raise ServiceException.bad_request("매매가 비활성화되어 있습니다 (TRADING_ENABLED=false)")

    async def get_account_balance(self) -> AccountBalance:
        """계좌 잔고 조회 (account_manager가 KIS 원본 키 정규화 담당)"""
        return await account_manager.get_balance()

    async def execute_order(self, request: OrderRequest) -> OrderResult:
        await self.check_trading_enabled()

        # 주문 금액 한도는 AI 리스크 매니저가 사전 검증 (시스템 하드 리밋 없음)

        response = await mcp_client.place_order(
            symbol=request.symbol,
            side=request.side.value,
            quantity=request.quantity,
            price=request.price,
            market=request.market.value,
        )

        if not response.success:
            logger.error("주문 실행 실패: {} - {}", request.symbol, response.error)
            return OrderResult(success=False, message=response.error or "주문 실패")

        data = response.data or {}
        logger.info(
            "주문 실행 성공: {} {} {} x{}",
            request.symbol, request.side.value, request.order_type.value, request.quantity,
        )
        return OrderResult(
            success=True,
            order_id=data.get("order_id"),
            message="주문 실행 완료",
            filled_quantity=int(data.get("filled_quantity", 0)),
            filled_price=float(data.get("filled_price", 0)),
        )

    async def get_current_price(self, symbol: str, market: str = "KRX") -> dict:
        response = await mcp_client.get_current_price(symbol, market)
        if not response.success:
            raise ServiceException.internal_server_error(f"현재가 조회 실패: {response.error}")
        return response.data or {}
