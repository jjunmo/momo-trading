from loguru import logger

from exceptions.common import ServiceException
from models.analysis import AnalysisResult
from repositories.analysis_repository import AnalysisRepository


class AnalysisService:
    def __init__(self, analysis_repo: AnalysisRepository):
        self.analysis_repo = analysis_repo

    async def get_all(self, limit: int = 20) -> list[AnalysisResult]:
        return await self.analysis_repo.get_recent(limit)

    async def get_by_id(self, analysis_id: str) -> AnalysisResult:
        result = await self.analysis_repo.get_by_id(analysis_id)
        if not result:
            raise ServiceException.not_found(f"분석 결과를 찾을 수 없습니다: {analysis_id}")
        return result

    async def get_by_stock(self, stock_id: str, limit: int = 10) -> list[AnalysisResult]:
        return await self.analysis_repo.get_by_stock(stock_id, limit)

    async def get_latest_by_stock(self, stock_id: str) -> AnalysisResult | None:
        return await self.analysis_repo.get_latest_by_stock(stock_id)

    async def save(self, analysis: AnalysisResult) -> AnalysisResult:
        created = await self.analysis_repo.create(analysis)
        logger.info(
            "분석 결과 저장: stock_id={}, recommendation={}, confidence={:.2f}",
            analysis.stock_id, analysis.recommendation, analysis.confidence,
        )
        return created
