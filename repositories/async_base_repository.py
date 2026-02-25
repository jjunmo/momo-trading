from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from uuid import UUID

from sqlalchemy import asc, desc, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class AsyncBaseRepository(Generic[T]):
    def __init__(self, model: Type[T], db: AsyncSession):
        self.model = model
        self.db = db

    async def get_by_id(self, id: UUID) -> Optional[T]:
        result = await self.db.execute(
            select(self.model).filter(self.model.id == id)
        )
        return result.scalars().first()

    async def get_all(self, skip: int = 0, limit: int = 100) -> List[T]:
        result = await self.db.execute(
            select(self.model).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, obj: T) -> T:
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def update(self, obj: T) -> T:
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def delete(self, obj: T) -> None:
        await self.db.delete(obj)
        await self.db.flush()

    async def bulk_insert(self, mappings: List[Dict[str, Any]]) -> None:
        await self.db.execute(insert(self.model), mappings)
        await self.db.flush()

    async def filter_by(self, **kwargs) -> List[T]:
        result = await self.db.execute(
            select(self.model).filter_by(**kwargs)
        )
        return list(result.scalars().all())

    async def filter_by_one(self, **kwargs) -> Optional[T]:
        result = await self.db.execute(
            select(self.model).filter_by(**kwargs)
        )
        return result.scalars().first()

    async def count(self, **kwargs) -> int:
        query = select(func.count()).select_from(self.model)
        if kwargs:
            query = query.filter_by(**kwargs)
        result = await self.db.execute(query)
        return result.scalar()

    async def order_by(self, column: str, direction: str = "desc") -> List[T]:
        sort_column = getattr(self.model, column, None)
        if not sort_column:
            return []
        query = select(self.model)
        query = query.order_by(
            desc(sort_column) if direction == "desc" else asc(sort_column)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())
