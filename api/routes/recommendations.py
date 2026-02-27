from fastapi import APIRouter

from dependencies.services import RecommendationServiceDep, RecommendationServiceTxDep
from schemas.common import SuccessResponse
from schemas.recommendation_schema import RecommendationResponse

router = APIRouter(prefix="/recommendations", tags=["AI 추천"])


@router.get("", response_model=SuccessResponse[list[RecommendationResponse]])
async def get_recommendations(service: RecommendationServiceDep, limit: int = 20):
    recs = await service.get_all(limit=limit)
    return SuccessResponse(data=[RecommendationResponse.model_validate(r) for r in recs])


@router.get("/pending", response_model=SuccessResponse[list[RecommendationResponse]])
async def get_pending_recommendations(service: RecommendationServiceDep):
    recs = await service.get_pending()
    return SuccessResponse(data=[RecommendationResponse.model_validate(r) for r in recs])


@router.get("/{rec_id}", response_model=SuccessResponse[RecommendationResponse])
async def get_recommendation(rec_id: str, service: RecommendationServiceDep):
    rec = await service.get_by_id(rec_id)
    return SuccessResponse(data=RecommendationResponse.model_validate(rec))


@router.post("/{rec_id}/approve", response_model=SuccessResponse[RecommendationResponse])
async def approve_recommendation(rec_id: str, service: RecommendationServiceTxDep):
    rec = await service.approve(rec_id)
    return SuccessResponse(data=RecommendationResponse.model_validate(rec), message="추천이 승인되었습니다")


@router.post("/{rec_id}/reject", response_model=SuccessResponse[RecommendationResponse])
async def reject_recommendation(rec_id: str, service: RecommendationServiceTxDep):
    rec = await service.reject(rec_id)
    return SuccessResponse(data=RecommendationResponse.model_validate(rec), message="추천이 거절되었습니다")
