from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class RecommendationResponse(BaseModel):
    id: str
    stock_id: str
    analysis_id: str
    action: str
    suggested_price: float
    suggested_quantity: int
    reason: str
    confidence: float
    status: str
    approved_at: Optional[datetime] = None
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
