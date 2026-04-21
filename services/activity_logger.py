"""활동 로거 — 큐 기반 순차 DB 저장 + SSE 브로드캐스트 (싱글턴, DI 비의존)

DB 쓰기를 큐로 분리하여 SQLite "database is locked" 충돌 원천 차단.
log() 호출 → 큐에 추가 (즉시 반환) → 전담 writer가 순차 배치 INSERT.
"""
import asyncio
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
    큐 기반 순차 쓰기로 SQLite 락 충돌 방지.
    """

    def __init__(self):
        self._queue: asyncio.Queue[AgentActivityLog] = asyncio.Queue()
        self._writer_task: asyncio.Task | None = None

    async def start_writer(self) -> None:
        """앱 시작 시 호출 — 전담 DB writer 태스크 시작"""
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._write_loop())
            logger.debug("ActivityLogger writer 시작")

    async def stop_writer(self) -> None:
        """앱 종료 시 — 남은 큐 flush 후 종료"""
        if self._writer_task:
            self._writer_task.cancel()
            self._writer_task = None
        # 남은 항목 마지막 flush
        await self._flush_queue()

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
        """활동 기록 → 큐에 추가 (즉시 반환) + SSE 브로드캐스트"""
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

        # 큐에 추가 (블로킹 없음, DB 접근 안 함)
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning("활동 로그 큐 가득 참 — 항목 버림: {}", summary[:50])

        # writer가 안 돌고 있으면 자동 시작 (lazy init)
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._write_loop())

        # SSE 브로드캐스트 (DB와 분리, 즉시 전송)
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

    async def _write_loop(self) -> None:
        """전담 writer — 큐에서 꺼내 순차 배치 INSERT (락 충돌 없음)"""
        while True:
            try:
                # 첫 항목 대기 (큐가 비면 여기서 블로킹)
                first = await self._queue.get()
                entries = [first]
                # 큐에 쌓인 나머지 모두 꺼내기 (배치)
                while not self._queue.empty():
                    try:
                        entries.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                # 한 트랜잭션으로 일괄 INSERT
                try:
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            session.add_all(entries)
                except Exception as e:
                    logger.error("활동 로그 배치 저장 실패 ({}건): {}", len(entries), str(e))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ActivityLogger writer 오류: {}", str(e))
                await asyncio.sleep(1)

    async def _flush_queue(self) -> None:
        """큐에 남은 항목 강제 flush"""
        entries = []
        while not self._queue.empty():
            try:
                entries.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if entries:
            try:
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        session.add_all(entries)
                logger.debug("ActivityLogger flush: {}건 저장", len(entries))
            except Exception as e:
                logger.error("ActivityLogger flush 실패 ({}건): {}", len(entries), str(e))

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
