from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from uuid import UUID

from sqlalchemy import asc, desc, insert
from sqlalchemy.orm import Session

T = TypeVar("T")


class BaseRepository(Generic[T]):
    def __init__(self, model: Type[T], db: Session):
        self.model = model
        self.db = db

    def get_by_id(self, id: UUID) -> Optional[T]:
        return self.db.query(self.model).filter(self.model.id == id).first()

    def get_all(self, skip: int = 0, limit: int = 100) -> List[T]:
        return self.db.query(self.model).offset(skip).limit(limit).all()

    def create(self, obj: T) -> T:
        self.db.add(obj)
        self.db.flush()
        return obj

    def update(self, obj: T) -> T:
        self.db.add(obj)
        self.db.flush()
        return obj

    def delete(self, obj: T) -> None:
        self.db.delete(obj)
        self.db.flush()

    def bulk_insert(self, mappings: List[Dict[str, Any]]) -> None:
        self.db.execute(insert(self.model), mappings)
        self.db.flush()

    def filter_by(self, **kwargs) -> List[T]:
        return self.db.query(self.model).filter_by(**kwargs).all()

    def filter_by_one(self, **kwargs) -> Optional[T]:
        return self.db.query(self.model).filter_by(**kwargs).first()

    def count(self, **kwargs) -> int:
        query = self.db.query(self.model)
        if kwargs:
            query = query.filter_by(**kwargs)
        return query.count()

    def order_by(self, column: str, direction: str = "desc") -> List[T]:
        sort_column = getattr(self.model, column, None)
        if not sort_column:
            return []
        query = self.db.query(self.model)
        if direction == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))
        return query.all()
