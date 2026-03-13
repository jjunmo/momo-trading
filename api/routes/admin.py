"""관리자 대시보드 API — SSE 스트림 + 활동 조회 + 설정 + 리포트 + 계좌"""
import asyncio
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from admin.sse_manager import sse_manager
from core.config import settings
from core.database import get_async_db
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository
from schemas.activity_schema import ActivityResponse, CycleResponse
from schemas.common import SuccessResponse
from schemas.daily_report_schema import DailyReportResponse
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType
from trading.mcp_client import mcp_client

router = APIRouter(prefix="/admin", tags=["admin"])


# ── SSE 실시간 스트림 ──
@router.get("/stream")
async def sse_stream():
    """SSE 실시간 활동 스트림"""
    client_id, queue = sse_manager.connect()

    async def event_generator():
        try:
            yield f"data: {{\"type\": \"connected\", \"client_id\": \"{client_id}\"}}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.disconnect(client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 활동 목록 ──
@router.get("/activities", response_model=SuccessResponse[list[ActivityResponse]])
async def get_activities(
    target_date: str | None = Query(None, description="YYYY-MM-DD"),
    cycle_id: str | None = Query(None),
    activity_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
):
    """활동 로그 목록 조회"""
    repo = AgentActivityRepository(db)

    if cycle_id:
        activities = await repo.get_by_cycle(cycle_id)
    elif target_date:
        d = date.fromisoformat(target_date)
        activities = await repo.get_by_date(d, limit=limit, offset=offset)
    elif activity_type:
        activities = await repo.get_by_type(activity_type, limit=limit)
    else:
        from util.time_util import now_kst
        activities = await repo.get_by_date(now_kst().date(), limit=limit, offset=offset)

    return SuccessResponse(data=activities)


# ── 사이클 목록 ──
@router.get("/cycles", response_model=SuccessResponse[list[CycleResponse]])
async def get_cycles(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """최근 사이클 목록"""
    repo = AgentActivityRepository(db)
    cycles = await repo.get_recent_cycles(limit)
    return SuccessResponse(data=cycles)


# ── 사이클 타임라인 ──
@router.get("/cycles/{cycle_id}/timeline", response_model=SuccessResponse[list[ActivityResponse]])
async def get_cycle_timeline(
    cycle_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """사이클 내 전체 활동 타임라인"""
    repo = AgentActivityRepository(db)
    activities = await repo.get_by_cycle(cycle_id)
    return SuccessResponse(data=activities)


# ── 일일 리포트 목록 ──
@router.get("/reports", response_model=SuccessResponse[list[DailyReportResponse]])
async def get_reports(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """일일 리포트 목록"""
    repo = DailyReportRepository(db)
    reports = await repo.get_reports(limit)
    return SuccessResponse(data=reports)


# ── 특정 날짜 리포트 ──
@router.get("/reports/latest", response_model=SuccessResponse[DailyReportResponse | None])
async def get_latest_report(db: AsyncSession = Depends(get_async_db)):
    """최신 리포트"""
    repo = DailyReportRepository(db)
    report = await repo.get_latest()
    return SuccessResponse(data=report)


@router.get("/reports/{report_date}", response_model=SuccessResponse[DailyReportResponse | None])
async def get_report_by_date(
    report_date: str,
    db: AsyncSession = Depends(get_async_db),
):
    """특정 날짜 리포트"""
    repo = DailyReportRepository(db)
    d = date.fromisoformat(report_date)
    report = await repo.get_by_date(d)
    return SuccessResponse(data=report)


# ── 계좌 정보 ──
@router.get("/account/balance")
async def get_account_balance():
    """계좌 잔고 조회"""
    try:
        balance = await account_manager.get_balance()
        return SuccessResponse(data={
            "total_asset": balance.total_asset,
            "cash": balance.cash,
            "stock_value": balance.stock_value,
            "total_pnl": balance.total_pnl,
            "total_pnl_rate": balance.total_pnl_rate,
        })
    except Exception as e:
        logger.error("계좌 잔고 조회 실패: {}", str(e))
        return SuccessResponse(data=None, message=f"잔고 조회 실패: {str(e)[:100]}")


@router.get("/account/holdings")
async def get_account_holdings():
    """보유 종목 조회"""
    try:
        holdings = await account_manager.get_holdings()
        return SuccessResponse(data=[
            {
                "symbol": h.symbol,
                "name": h.name,
                "quantity": h.quantity,
                "avg_buy_price": h.avg_buy_price,
                "current_price": h.current_price,
                "pnl": h.pnl,
                "pnl_rate": h.pnl_rate,
            }
            for h in holdings
        ])
    except Exception as e:
        logger.error("보유 종목 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"보유 종목 조회 실패: {str(e)[:100]}")


@router.get("/account/pending-orders")
async def get_pending_orders():
    """미체결 주문 조회"""
    try:
        orders = await account_manager.get_pending_orders()
        return SuccessResponse(data=[
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "name": o.name,
                "side": o.side,
                "order_qty": o.order_qty,
                "filled_qty": o.filled_qty,
                "remaining_qty": o.remaining_qty,
                "order_price": o.order_price,
                "order_time": o.order_time,
            }
            for o in orders
        ])
    except Exception as e:
        logger.error("미체결 주문 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"미체결 주문 조회 실패: {str(e)[:100]}")


# ── 설정 조회/변경 ──
MUTABLE_SETTINGS = [
    "TRADING_ENABLED", "AUTONOMY_MODE",
    "RECOMMENDATION_EXPIRE_MIN",
    "SCHEDULER_ENABLED",
    "RISK_APPETITE",
]


@router.get("/settings")
async def get_settings():
    """런타임 설정 조회"""
    data = {}
    for key in MUTABLE_SETTINGS:
        data[key] = getattr(settings, key, None)
    return SuccessResponse(data=data)


@router.put("/settings")
async def update_settings(updates: dict):
    """런타임 설정 변경 (재시작 불필요)"""
    changed = {}
    for key, value in updates.items():
        if key not in MUTABLE_SETTINGS:
            continue
        old = getattr(settings, key, None)
        # 타입 변환
        if isinstance(old, bool):
            value = str(value).lower() in ("true", "1", "yes")
        elif isinstance(old, int):
            value = int(value)
        elif isinstance(old, float):
            value = float(value)
        setattr(settings, key, value)
        changed[key] = {"old": old, "new": value}
        logger.info("설정 변경: {} = {} → {}", key, old, value)

    if changed:
        await activity_logger.log(
            ActivityType.EVENT, ActivityPhase.PROGRESS,
            f"\u2699\ufe0f 설정 변경: {', '.join(changed.keys())}",
            detail=changed,
        )

    return SuccessResponse(data=changed, message=f"{len(changed)}개 설정 변경됨")


# ── Claude Code 사용량 ──
@router.get("/llm/usage")
async def get_llm_usage():
    """Claude Code 구독 사용량 (stats-cache.json)"""
    import json
    from pathlib import Path

    stats_path = Path.home() / ".claude" / "stats-cache.json"
    if not stats_path.exists():
        return SuccessResponse(data=None, message="stats-cache.json 없음")

    try:
        data = json.loads(stats_path.read_text())

        # 앱의 실시간 사용량도 함께 반환
        from analysis.llm.claude_code_provider import ClaudeCodeProvider
        app_usage = ClaudeCodeProvider.get_usage_snapshot()

        return SuccessResponse(data={
            "total_sessions": data.get("totalSessions", 0),
            "total_messages": data.get("totalMessages", 0),
            "first_session_date": data.get("firstSessionDate"),
            "last_computed_date": data.get("lastComputedDate"),
            "model_usage": data.get("modelUsage", {}),
            "daily_activity": data.get("dailyActivity", []),
            "daily_model_tokens": data.get("dailyModelTokens", []),
            "app_usage": app_usage,
        })
    except Exception as e:
        logger.error("Claude 사용량 조회 실패: {}", str(e))
        return SuccessResponse(data=None, message=f"조회 실패: {str(e)[:100]}")


# ── LLM 상태 ──
@router.get("/llm/status")
async def get_llm_status():
    """LLM 프로바이더 상태 및 설정 조회"""
    from analysis.llm.llm_factory import llm_factory
    return SuccessResponse(data=llm_factory.get_llm_status())


# ── 시스템 상태 ──
@router.get("/system/status")
async def get_system_status():
    """시스템 전체 상태"""
    from agent.trading_agent import trading_agent
    from scheduler.scheduler import trading_scheduler

    from scheduler.market_calendar import market_calendar

    return SuccessResponse(data={
        "trading_enabled": settings.TRADING_ENABLED,
        "autonomy_mode": settings.AUTONOMY_MODE,
        "mcp_connected": mcp_client.is_connected,
        "scheduler_running": trading_scheduler.is_running,
        "agent_running": trading_agent._running,
        "last_cycle_time": trading_agent.last_cycle_time.isoformat() if trading_agent.last_cycle_time else None,
        "sse_clients": sse_manager.client_count,
        "environment": settings.ENVIRONMENT,
        "market_open": market_calendar.is_krx_trading_hours(),
        "market_holiday": market_calendar.get_holiday_name(),
        "next_market_open": market_calendar.next_krx_open().strftime("%m/%d %H:%M"),
    })


# ── 수동 사이클 트리거 ──
@router.post("/agent/trigger")
async def trigger_agent_cycle():
    """수동으로 에이전트 사이클 실행"""
    from agent.trading_agent import trading_agent

    await activity_logger.log(
        ActivityType.EVENT, ActivityPhase.PROGRESS,
        "\U0001f3ae 수동 사이클 트리거 (관리자)",
    )

    # 비동기로 실행 (즉시 응답)
    asyncio.create_task(trading_agent.run_cycle())
    return SuccessResponse(message="에이전트 사이클이 트리거되었습니다")


# ── 수동 일일 리포트 생성 ──
@router.post("/reports/generate")
async def generate_report(target_date: str | None = Query(None)):
    """수동 일일 리포트 생성"""
    from services.daily_report_service import daily_report_service
    d = date.fromisoformat(target_date) if target_date else None
    report = await daily_report_service.generate_daily_report(d)
    if report:
        return SuccessResponse(
            data=DailyReportResponse.model_validate(report),
            message="리포트 생성 완료",
        )
    return SuccessResponse(message="리포트 생성 실패 또는 이미 존재")
