"""활동 로거 — DB 저장 + SSE 브로드캐스트 (싱글턴, DI 비의존)"""
import json
import time
from uuid import uuid4

from loguru import logger

from admin.sse_manager import sse_manager
from core.database import AsyncSessionLocal
from models.agent_activity import AgentActivityLog
from trading.enums import ActivityPhase, ActivityType
from util.time_util import now_kst


class ActivityLogger:
    """
    에이전트 활동을 DB에 기록하고 SSE로 실시간 전송.
    자체 세션을 생성하므로 DI 없이 어디서든 호출 가능.
    """

    async def log(
        self,
        activity_type: ActivityType | str,
        phase: ActivityPhase | str,
        summary: str,
        *,
        cycle_id: str | None = None,
        stock_id: str | None = None,
        symbol: str | None = None,
        detail: dict | None = None,
        llm_provider: str | None = None,
        llm_tier: str | None = None,
        execution_time_ms: int | None = None,
        confidence: float | None = None,
        error_message: str | None = None,
    ) -> AgentActivityLog | None:
        """활동 기록 → DB 저장 + SSE 브로드캐스트"""
        detail_json = json.dumps(detail, ensure_ascii=False, default=str) if detail else None
        ts = now_kst()

        entry = AgentActivityLog(
            created_at=ts,
            cycle_id=cycle_id,
            activity_type=activity_type,
            phase=phase,
            stock_id=stock_id,
            symbol=symbol,
            summary=summary,
            detail=detail_json,
            llm_provider=llm_provider,
            llm_tier=llm_tier,
            execution_time_ms=execution_time_ms,
            confidence=confidence,
            error_message=error_message,
        )

        # DB 저장 (자체 세션)
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    session.add(entry)
        except Exception as e:
            logger.error("활동 로그 DB 저장 실패: {}", str(e))

        # SSE 브로드캐스트
        try:
            await sse_manager.broadcast({
                "type": "activity",
                "data": {
                    "id": entry.id,
                    "cycle_id": cycle_id,
                    "activity_type": activity_type,
                    "phase": phase,
                    "symbol": symbol,
                    "summary": summary,
                    "detail": detail_json,
                    "llm_provider": llm_provider,
                    "llm_tier": llm_tier,
                    "execution_time_ms": execution_time_ms,
                    "confidence": confidence,
                    "error_message": error_message,
                    "created_at": ts.isoformat(),
                },
            })
        except Exception as e:
            logger.error("SSE 브로드캐스트 실패: {}", str(e))

        return entry

    def start_cycle(self) -> str:
        """새 사이클 ID 생성"""
        return str(uuid4())

    @staticmethod
    def timer() -> float:
        """실행 시간 측정용 타이머 시작"""
        return time.time()

    @staticmethod
    def elapsed_ms(start: float) -> int:
        """타이머로부터 경과 ms"""
        return int((time.time() - start) * 1000)


activity_logger = ActivityLogger()
