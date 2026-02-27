from loguru import logger

from exceptions.common import ServiceException
from models.stock import Stock
from repositories.stock_repository import StockRepository
from schemas.stock_schema import StockCreate, StockUpdate


class StockService:
    def __init__(self, stock_repo: StockRepository):
        self.stock_repo = stock_repo

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[Stock]:
        return await self.stock_repo.get_all(skip=skip, limit=limit)

    async def get_by_id(self, stock_id: str) -> Stock:
        stock = await self.stock_repo.get_by_id(stock_id)
        if not stock:
            raise ServiceException.not_found(f"종목을 찾을 수 없습니다: {stock_id}")
        return stock

    async def get_by_symbol(self, symbol: str) -> Stock:
        stock = await self.stock_repo.get_by_symbol(symbol)
        if not stock:
            raise ServiceException.not_found(f"종목을 찾을 수 없습니다: {symbol}")
        return stock

    async def get_by_market(self, market: str) -> list[Stock]:
        return await self.stock_repo.get_by_market(market)

    async def search(self, name: str) -> list[Stock]:
        return await self.stock_repo.search_by_name(name)

    async def create(self, data: StockCreate) -> Stock:
        existing = await self.stock_repo.get_by_symbol(data.symbol)
        if existing:
            raise ServiceException.conflict(f"이미 존재하는 종목입니다: {data.symbol}")
        stock = Stock(
            symbol=data.symbol,
            name=data.name,
            market=data.market.value,
            category=data.category,
            exchange_code=data.exchange_code,
        )
        created = await self.stock_repo.create(stock)
        logger.info("종목 등록: {} ({})", data.symbol, data.name)
        return created

    async def update(self, stock_id: str, data: StockUpdate) -> Stock:
        stock = await self.get_by_id(stock_id)
        if data.name is not None:
            stock.name = data.name
        if data.category is not None:
            stock.category = data.category
        if data.is_active is not None:
            stock.is_active = data.is_active
        return await self.stock_repo.update(stock)

    async def get_or_create(self, symbol: str, name: str, market: str) -> Stock:
        stock = await self.stock_repo.get_by_symbol(symbol)
        if stock:
            return stock
        stock = Stock(symbol=symbol, name=name, market=market)
        return await self.stock_repo.create(stock)
