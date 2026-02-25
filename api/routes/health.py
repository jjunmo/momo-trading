from fastapi import APIRouter

from schemas.common import SuccessResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=SuccessResponse[dict])
async def health_check():
    return SuccessResponse(data={"status": "healthy"}, message="서버가 정상 작동 중입니다")
