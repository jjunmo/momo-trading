from fastapi import APIRouter, Query

from dependencies.services import OrderServiceDep, OrderServiceTxDep
from schemas.common import SuccessResponse
from schemas.order_schema import OrderCreate, OrderResponse

router = APIRouter(prefix="/orders", tags=["주문"])


@router.get("", response_model=SuccessResponse[list[OrderResponse]])
async def get_orders(
    service: OrderServiceDep,
    portfolio_id: str | None = Query(None),
    skip: int = 0,
    limit: int = 50,
):
    if portfolio_id:
        orders = await service.get_by_portfolio(portfolio_id, skip=skip, limit=limit)
    else:
        orders = await service.get_all(skip=skip, limit=limit)
    return SuccessResponse(data=[OrderResponse.model_validate(o) for o in orders])


@router.post("", response_model=SuccessResponse[OrderResponse], status_code=201)
async def create_order(data: OrderCreate, service: OrderServiceTxDep):
    order = await service.create(data)
    return SuccessResponse(data=OrderResponse.model_validate(order), message="주문이 생성되었습니다")


@router.get("/{order_id}", response_model=SuccessResponse[OrderResponse])
async def get_order(order_id: str, service: OrderServiceDep):
    order = await service.get_by_id(order_id)
    return SuccessResponse(data=OrderResponse.model_validate(order))


@router.delete("/{order_id}", response_model=SuccessResponse[OrderResponse])
async def cancel_order(order_id: str, service: OrderServiceTxDep):
    order = await service.cancel(order_id)
    return SuccessResponse(data=OrderResponse.model_validate(order), message="주문이 취소되었습니다")
