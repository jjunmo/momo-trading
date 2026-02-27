"""일일 리포트 리포지토리"""
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.daily_report import DailyReport
from repositories.async_base_repository import AsyncBaseRepository


class DailyReportRepository(AsyncBaseRepository[DailyReport]):
    def __init__(self, db: AsyncSession):
        super().__init__(DailyReport, db)

    async def get_by_date(self, report_date: date) -> Optional[DailyReport]:
        return await self.filter_by_one(report_date=report_date)

    async def get_latest(self) -> Optional[DailyReport]:
        result = await self.db.execute(
            select(DailyReport).order_by(DailyReport.report_date.desc()).limit(1)
        )
        return result.scalars().first()

    async def get_reports(self, limit: int = 30) -> list[DailyReport]:
        result = await self.db.execute(
            select(DailyReport).order_by(DailyReport.report_date.desc()).limit(limit)
        )
        return list(result.scalars().all())
