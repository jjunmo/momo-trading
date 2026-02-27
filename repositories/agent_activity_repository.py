"""에이전트 활동 로그 리포지토리"""
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_activity import AgentActivityLog
from repositories.async_base_repository import AsyncBaseRepository


class AgentActivityRepository(AsyncBaseRepository[AgentActivityLog]):
    def __init__(self, db: AsyncSession):
        super().__init__(AgentActivityLog, db)

    async def get_by_date(
        self, target_date: date, limit: int = 500, offset: int = 0
    ) -> list[AgentActivityLog]:
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)
        result = await self.db.execute(
            select(AgentActivityLog)
            .where(AgentActivityLog.created_at.between(start, end))
            .order_by(AgentActivityLog.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_cycle(self, cycle_id: str) -> list[AgentActivityLog]:
        result = await self.db.execute(
            select(AgentActivityLog)
            .where(AgentActivityLog.cycle_id == cycle_id)
            .order_by(AgentActivityLog.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_type(
        self, activity_type: str, limit: int = 100
    ) -> list[AgentActivityLog]:
        result = await self.db.execute(
            select(AgentActivityLog)
            .where(AgentActivityLog.activity_type == activity_type)
            .order_by(AgentActivityLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent_cycles(self, limit: int = 20) -> list[dict]:
        """최근 사이클 목록 (cycle_id별 그룹핑)"""
        result = await self.db.execute(
            select(
                AgentActivityLog.cycle_id,
                func.min(AgentActivityLog.created_at).label("started_at"),
                func.max(AgentActivityLog.created_at).label("ended_at"),
                func.count(AgentActivityLog.id).label("activity_count"),
            )
            .where(AgentActivityLog.cycle_id.isnot(None))
            .group_by(AgentActivityLog.cycle_id)
            .order_by(func.max(AgentActivityLog.created_at).desc())
            .limit(limit)
        )
        return [
            {
                "cycle_id": row.cycle_id,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "activity_count": row.activity_count,
            }
            for row in result.all()
        ]

    async def count_by_date(self, target_date: date) -> dict:
        """날짜별 활동 유형 카운트"""
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time.max)
        result = await self.db.execute(
            select(
                AgentActivityLog.activity_type,
                func.count(AgentActivityLog.id).label("count"),
            )
            .where(AgentActivityLog.created_at.between(start, end))
            .group_by(AgentActivityLog.activity_type)
        )
        return {row.activity_type: row.count for row in result.all()}
