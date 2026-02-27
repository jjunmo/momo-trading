from fastapi import APIRouter, Query

from dependencies.services import StockServiceDep, StockServiceTxDep
from schemas.common import SuccessResponse
from schemas.stock_schema import StockCreate, StockResponse

router = APIRouter(prefix="/stocks", tags=["종목"])


@router.get("", response_model=SuccessResponse[list[StockResponse]])
async def get_stocks(
    service: StockServiceDep,
    market: str | None = Query(None, description="시장 필터 (KOSPI, NASDAQ 등)"),
    search: str | None = Query(None, description="종목명 검색"),
    skip: int = 0,
    limit: int = 100,
):
    if search:
        stocks = await service.search(search)
    elif market:
        stocks = await service.get_by_market(market)
    else:
        stocks = await service.get_all(skip=skip, limit=limit)
    return SuccessResponse(data=[StockResponse.model_validate(s) for s in stocks])


@router.get("/{stock_id}", response_model=SuccessResponse[StockResponse])
async def get_stock(stock_id: str, service: StockServiceDep):
    stock = await service.get_by_id(stock_id)
    return SuccessResponse(data=StockResponse.model_validate(stock))


@router.post("", response_model=SuccessResponse[StockResponse], status_code=201)
async def create_stock(data: StockCreate, service: StockServiceTxDep):
    stock = await service.create(data)
    return SuccessResponse(data=StockResponse.model_validate(stock), message="종목이 등록되었습니다")
