"""MCP를 통한 주문 실행/취소/조회"""
from loguru import logger

from core.config import settings
from trading.mcp_client import mcp_client
from trading.models import OrderRequest, OrderResult


class OrderExecutor:
    """MCP를 통한 주문 실행기"""

    async def execute(self, request: OrderRequest) -> OrderResult:
        """주문 실행"""
        if not settings.TRADING_ENABLED:
            return OrderResult(success=False, message="매매가 비활성화되어 있습니다")

        response = await mcp_client.place_order(
            symbol=request.symbol,
            side=request.side.value,
            quantity=request.quantity,
            price=request.price,
            market=request.market.value,
        )

        if not response.success:
            logger.error("주문 실행 실패: {} {}", request.symbol, response.error)
            return OrderResult(success=False, message=response.error or "주문 실패")

        data = response.data or {}
        logger.info(
            "주문 실행: {} {} x{} @ {}",
            request.symbol, request.side.value, request.quantity, request.price,
        )
        return OrderResult(
            success=True,
            order_id=data.get("order_id"),
            message="주문 실행 완료",
            filled_quantity=int(data.get("filled_quantity", 0)),
            filled_price=float(data.get("filled_price", 0)),
        )

    async def cancel(self, order_id: str, market: str = "KRX") -> OrderResult:
        """주문 취소 (KIS REST API 직접 호출)"""
        from trading.kis_api import cancel_order_direct

        result = await cancel_order_direct(order_id=order_id)
        if not result.get("success"):
            return OrderResult(success=False, message=result.get("error", "취소 실패"))

        logger.info("주문 취소: {}", order_id)
        return OrderResult(success=True, message="주문 취소 완료")


order_executor = OrderExecutor()
