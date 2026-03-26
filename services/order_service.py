from loguru import logger

from exceptions.common import ServiceException
from models.order import Order
from repositories.order_repository import OrderRepository
from repositories.stock_repository import StockRepository
from schemas.order_schema import OrderCreate


class OrderService:
    def __init__(
        self,
        order_repo: OrderRepository,
        stock_repo: StockRepository,
    ):
        self.order_repo = order_repo
        self.stock_repo = stock_repo

    async def get_all(self, skip: int = 0, limit: int = 50) -> list[Order]:
        return await self.order_repo.get_all(skip=skip, limit=limit)

    async def get_by_id(self, order_id: str) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise ServiceException.not_found(f"주문을 찾을 수 없습니다: {order_id}")
        return order

    async def get_by_portfolio(
        self, portfolio_id: str, skip: int = 0, limit: int = 50
    ) -> list[Order]:
        return await self.order_repo.get_by_portfolio(portfolio_id, skip=skip, limit=limit)

    async def create(self, data: OrderCreate) -> Order:
        stock = await self.stock_repo.get_by_symbol(data.symbol)
        if not stock:
            raise ServiceException.not_found(f"종목을 찾을 수 없습니다: {data.symbol}")

        order = Order(
            portfolio_id=data.portfolio_id,
            stock_id=stock.id,
            side=data.side.value,
            order_type=data.order_type.value,
            status="PENDING",
            source="MANUAL",
            quantity=data.quantity,
            price=data.price,
            reason=data.reason,
        )
        created = await self.order_repo.create(order)
        logger.debug("주문 생성: {} {} {} x{}", data.symbol, data.side.value, data.order_type.value, data.quantity)
        return created

    async def cancel(self, order_id: str) -> Order:
        order = await self.get_by_id(order_id)
        if order.status not in ("PENDING", "SUBMITTED"):
            raise ServiceException.bad_request(f"취소할 수 없는 주문 상태입니다: {order.status}")
        order.status = "CANCELLED"
        return await self.order_repo.update(order)

    async def get_pending_orders(self) -> list[Order]:
        return await self.order_repo.get_pending_orders()
