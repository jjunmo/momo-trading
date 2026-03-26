"""Q&A 스키마"""
from pydantic import BaseModel, Field


class QARequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    cycle_id: str | None = None
    symbol: str | None = None


class QAResponse(BaseModel):
    question: str
    answer: str
    context_summary: str
    llm_provider: str
    execution_time_ms: int
