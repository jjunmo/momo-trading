"""활동 로그 스키마"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from util.time_util import ensure_kst


class ActivityResponse(BaseModel):
    id: str
    cycle_id: Optional[str] = None
    activity_type: str
    phase: str
    stock_id: Optional[str] = None
    symbol: Optional[str] = None
    summary: str
    detail: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_tier: Optional[str] = None
    execution_time_ms: Optional[int] = None
    confidence: Optional[float] = None
    error_message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_kst_created_at(cls, v):
        return ensure_kst(v)


class CycleResponse(BaseModel):
    cycle_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    activity_count: int
    summary: Optional[str] = None

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def ensure_kst_cycle_times(cls, v):
        return ensure_kst(v) if v else v


class ActivityFilter(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD
    cycle_id: Optional[str] = None
    activity_type: Optional[str] = None
    limit: int = 100
    offset: int = 0
