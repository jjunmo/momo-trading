from loguru import logger

from exceptions.common import ServiceException
from models.recommendation import Recommendation
from repositories.recommendation_repository import RecommendationRepository
from util.time_util import now_kst


class RecommendationService:
    def __init__(self, recommendation_repo: RecommendationRepository):
        self.recommendation_repo = recommendation_repo

    async def get_all(self, limit: int = 20) -> list[Recommendation]:
        return await self.recommendation_repo.get_recent(limit)

    async def get_by_id(self, rec_id: str) -> Recommendation:
        rec = await self.recommendation_repo.get_by_id(rec_id)
        if not rec:
            raise ServiceException.not_found(f"추천을 찾을 수 없습니다: {rec_id}")
        return rec

    async def get_pending(self) -> list[Recommendation]:
        return await self.recommendation_repo.get_pending()

    async def create(self, recommendation: Recommendation) -> Recommendation:
        created = await self.recommendation_repo.create(recommendation)
        logger.info(
            "AI 추천 생성: stock_id={}, action={}, confidence={:.2f}",
            recommendation.stock_id, recommendation.action, recommendation.confidence,
        )
        return created

    async def approve(self, rec_id: str) -> Recommendation:
        rec = await self.get_by_id(rec_id)
        if rec.status != "PENDING":
            raise ServiceException.bad_request(f"승인할 수 없는 상태입니다: {rec.status}")
        now = now_kst()
        if rec.expires_at < now:
            rec.status = "EXPIRED"
            await self.recommendation_repo.update(rec)
            raise ServiceException.bad_request("만료된 추천입니다")
        rec.status = "APPROVED"
        rec.approved_at = now
        updated = await self.recommendation_repo.update(rec)
        logger.debug("AI 추천 승인: {}", rec_id)
        return updated

    async def reject(self, rec_id: str) -> Recommendation:
        rec = await self.get_by_id(rec_id)
        if rec.status != "PENDING":
            raise ServiceException.bad_request(f"거절할 수 없는 상태입니다: {rec.status}")
        rec.status = "REJECTED"
        updated = await self.recommendation_repo.update(rec)
        logger.debug("AI 추천 거절: {}", rec_id)
        return updated

    async def expire_overdue(self) -> int:
        now = now_kst()
        expired = await self.recommendation_repo.get_expired(now)
        count = 0
        for rec in expired:
            rec.status = "EXPIRED"
            await self.recommendation_repo.update(rec)
            count += 1
        if count > 0:
            logger.debug("만료된 추천 {} 건 처리", count)
        return count
