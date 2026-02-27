from fastapi import APIRouter, Query

from dependencies.services import AnalysisServiceDep
from schemas.common import SuccessResponse
from schemas.analysis_schema import AnalysisResponse

router = APIRouter(prefix="/analysis", tags=["분석"])


@router.get("", response_model=SuccessResponse[list[AnalysisResponse]])
async def get_analyses(
    service: AnalysisServiceDep,
    stock_id: str | None = Query(None),
    limit: int = 20,
):
    if stock_id:
        results = await service.get_by_stock(stock_id, limit=limit)
    else:
        results = await service.get_all(limit=limit)
    return SuccessResponse(data=[AnalysisResponse.model_validate(r) for r in results])


@router.get("/{analysis_id}", response_model=SuccessResponse[AnalysisResponse])
async def get_analysis(analysis_id: str, service: AnalysisServiceDep):
    result = await service.get_by_id(analysis_id)
    return SuccessResponse(data=AnalysisResponse.model_validate(result))
