from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AnalysisRunRequest(BaseModel):
    stock_id: str
    type: str = "COMPREHENSIVE"


class AnalysisResponse(BaseModel):
    id: str
    stock_id: str
    type: str
    recommendation: str
    confidence: float
    summary: str
    detail: Optional[str] = None
    llm_provider: str
    llm_tier: str
    target_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}
