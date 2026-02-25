from datetime import datetime
from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Result(str, Enum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"


class SuccessResponse(BaseModel, Generic[T]):
    result: str = Result.SUCCESS
    data: Optional[T] = None
    message: Optional[str] = None


class BasicErrorResponse(BaseModel):
    result: str = Result.FAIL
    errorCode: str
    message: str
    data: Optional[dict] = None
    timestamp: Optional[datetime] = None
    request_id: Optional[str] = None
    path: Optional[str] = None
