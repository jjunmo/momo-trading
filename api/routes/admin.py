"""관리자 대시보드 API — SSE 스트림 + 활동 조회 + 설정 + 리포트 + 계좌 + Q&A"""
import asyncio
import json as _json
import time as _time
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
from schemas.qa_schema import QARequest, QAResponse
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


# ── 종목명 매핑 ──
@router.get("/symbol-names")
async def get_symbol_names():
    """종목 심볼 → 이름 매핑 (프론트엔드 표시용)"""
    from agent.trading_agent import trading_agent
    return SuccessResponse(data=trading_agent._symbol_names)


# ── 활동 목록 ──
@router.get("/activities", response_model=SuccessResponse[list[ActivityResponse]])
async def get_activities(
    target_date: str | None = Query(None, description="YYYY-MM-DD"),
    cycle_id: str | None = Query(None),
    activity_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=2000),
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


# ── 매매 내역 ──
@router.get("/trades")
async def get_trades(
    target_date: str | None = Query(None, description="조회 날짜 (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
):
    """특정 날짜의 매매 내역 (매수 진입 + 청산 완료)"""
    from repositories.trade_result_repository import TradeResultRepository
    from schemas.feedback_schema import TradeResultResponse
    from util.time_util import now_kst

    d = date.fromisoformat(target_date) if target_date else now_kst().date()
    repo = TradeResultRepository(db)

    # 오늘 진입한 매수
    opened = await repo.get_opened_by_date(d)
    # 오늘 청산된 포지션 (BUY 레코드, pnl 계산됨)
    completed = await repo.get_completed_by_date(d)
    # 미청산 포지션
    open_positions = await repo.get_all_open()

    return SuccessResponse(data={
        "date": str(d),
        "opened": [TradeResultResponse.model_validate(t) for t in opened],
        "completed": [TradeResultResponse.model_validate(t) for t in completed],
        "open_positions": [TradeResultResponse.model_validate(t) for t in open_positions],
    })


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
    """보유 종목 조회 (KRX/NXT 시세 구분 포함)"""
    try:
        from scheduler.market_calendar import market_calendar
        from agent.market_scanner import market_scanner

        holdings = await account_manager.get_holdings()
        session = market_calendar.get_market_session()
        untradeable = market_scanner._untradeable_symbols

        def _market_label(symbol: str) -> str:
            if session in ("NXT_PRE", "NXT_AFTER"):
                if symbol in untradeable:
                    return "KRX_ONLY"
                return "NXT"
            return "KRX"

        return SuccessResponse(data=[
            {
                "symbol": h.symbol,
                "name": h.name,
                "quantity": h.quantity,
                "avg_buy_price": h.avg_buy_price,
                "current_price": h.current_price,
                "pnl": h.pnl,
                "pnl_rate": h.pnl_rate,
                "tradeable_market": _market_label(h.symbol),
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


@router.post("/sync-holdings")
async def sync_holdings():
    """KIS 계좌 보유 종목 → trade_results BUY 레코드 복원"""
    from uuid import uuid4
    from models.trade_result import TradeResult
    from repositories.trade_result_repository import TradeResultRepository
    from core.database import AsyncSessionLocal
    from util.time_util import now_kst

    try:
        holdings = await account_manager.get_holdings()
        if not holdings:
            return SuccessResponse(data={"synced": 0, "message": "보유 종목 없음"})

        now = now_kst()
        synced = []

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open()
                db_symbols = {tr.stock_symbol for tr in open_positions}

                for h in holdings:
                    if h.quantity <= 0 or h.symbol in db_symbols:
                        continue
                    record = TradeResult(
                        id=str(uuid4()),
                        stock_symbol=h.symbol,
                        stock_name=h.name,
                        side="BUY",
                        strategy_type="STABLE_SHORT",
                        entry_price=h.avg_buy_price,
                        exit_price=0,
                        quantity=h.quantity,
                        pnl=0,
                        return_pct=0,
                        is_win=False,
                        hold_days=0,
                        exit_reason="",
                        ai_recommendation="BUY",
                        ai_confidence=0.5,
                        market="KRX",
                        market_regime="",
                        status="CONFIRMED",
                        entry_at=now,
                        notes="KIS 계좌 동기화 복원",
                    )
                    session.add(record)
                    synced.append({"symbol": h.symbol, "name": h.name,
                                   "qty": h.quantity, "avg_price": h.avg_buy_price})
                    logger.info("보유 종목 복원: {}({}) {}주 @{:,.0f}원",
                                h.name, h.symbol, h.quantity, h.avg_buy_price)

        return SuccessResponse(data={"synced": len(synced), "details": synced})
    except Exception as e:
        logger.error("보유 종목 동기화 실패: {}", str(e))
        return SuccessResponse(data={"synced": 0, "error": str(e)})


@router.post("/cancel-all-pending")
async def cancel_all_pending():
    """미체결 주문 전체 취소 — 묶인 현금 해방"""
    from trading.kis_api import cancel_order_direct

    try:
        orders = await account_manager.get_pending_orders()
        if not orders:
            return SuccessResponse(data={"cancelled": 0, "message": "미체결 주문 없음"})

        results = []
        for o in orders:
            if not o.order_id:
                continue
            cancel_result = await cancel_order_direct(order_id=o.order_id)
            results.append({
                "order_id": o.order_id,
                "symbol": o.symbol,
                "name": o.name,
                "success": cancel_result.get("success", False),
                "message": cancel_result.get("error", "취소 완료"),
            })
            logger.info("미체결 취소: {} {} — {}", o.symbol, o.order_id,
                        "성공" if cancel_result.get("success") else cancel_result.get("error"))

        cancelled = sum(1 for r in results if r["success"])
        return SuccessResponse(data={
            "cancelled": cancelled,
            "total": len(results),
            "details": results,
        })
    except Exception as e:
        logger.error("미체결 전체 취소 실패: {}", str(e))
        return SuccessResponse(data={"cancelled": 0, "error": str(e)})


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


# ── LLM 사용량 ──
@router.get("/llm/usage")
async def get_llm_usage():
    """LLM provider 사용량."""
    import json
    from pathlib import Path

    from analysis.llm.llm_factory import llm_factory

    app_usage = llm_factory.get_llm_usage()
    stats_path = Path.home() / ".claude" / "stats-cache.json"
    if not stats_path.exists():
        return SuccessResponse(data={"app_usage": app_usage}, message="Claude stats-cache.json 없음")

    try:
        data = json.loads(stats_path.read_text())

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
        logger.error("LLM 사용량 조회 실패: {}", str(e))
        return SuccessResponse(data={"app_usage": app_usage}, message=f"조회 실패: {str(e)[:100]}")


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
        "market_open": market_calendar.is_domestic_trading_hours(),
        "market_session": market_calendar.get_market_session(),
        "market_holiday": market_calendar.get_holiday_name(),
        "next_market_open": market_calendar.next_market_open().strftime("%m/%d %H:%M"),
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


# ── Q&A: 분석 결과에 대한 질문 ──
@router.post("/qa/ask", response_model=SuccessResponse[QAResponse])
async def ask_question(
    req: QARequest,
    db: AsyncSession = Depends(get_async_db),
):
    """활동 기록 기반 Q&A — LLM(Tier1)으로 답변"""
    start = _time.time()
    repo = AgentActivityRepository(db)

    # 1. 컨텍스트 결정: cycle_id → symbol → 오늘 전체
    if req.cycle_id:
        activities = await repo.get_by_cycle(req.cycle_id)
        context_label = f"사이클 {req.cycle_id[:8]}"
    elif req.symbol:
        activities = await repo.get_by_symbol(req.symbol, limit=50)
        context_label = f"종목 {req.symbol}"
    else:
        from util.time_util import now_kst
        activities = await repo.get_by_date(now_kst().date(), limit=100)
        context_label = "오늘 전체"

    # 2. 핵심 필드 추출 (프롬프트에 전달할 컨텍스트)
    context_lines = []
    for a in activities[-80:]:  # 최근 80건 (역순 → 시간순)
        line = f"[{a.activity_type}/{a.phase}] {a.summary}"
        if a.symbol:
            line = f"[{a.symbol}] " + line
        if a.confidence is not None:
            line += f" (확신도: {a.confidence:.0%})"
        # detail에서 recommendation, reason 추출
        if a.detail:
            try:
                detail = _json.loads(a.detail) if isinstance(a.detail, str) else a.detail
                for key in ("recommendation", "reason", "signal", "action", "exit_reason"):
                    if key in detail:
                        line += f" | {key}: {detail[key]}"
            except (ValueError, TypeError):
                pass
        context_lines.append(line)

    context_text = "\n".join(context_lines) if context_lines else "(활동 기록 없음)"

    # 2.5 포트폴리오 실시간 컨텍스트
    portfolio_text = ""
    try:
        balance, holdings = await account_manager.get_account_snapshot()
        parts = []
        if balance.is_valid:
            parts.append(
                f"총자산: {balance.total_asset:,.0f}원 | 현금: {balance.cash:,.0f}원 | "
                f"주식평가: {balance.stock_value:,.0f}원 | 총손익: {balance.total_pnl:+,.0f}원 ({balance.total_pnl_rate:+.2f}%)"
            )
        if holdings:
            parts.append(f"보유 {len(holdings)}종목:")
            for h in holdings:
                parts.append(f"- {h.name}({h.symbol}) {h.quantity}주 평균단가:{h.avg_buy_price:,.0f} 현재가:{h.current_price:,.0f} 수익률:{h.pnl_rate:+.2f}%")
        else:
            parts.append("보유 종목 없음")
        portfolio_text = "\n".join(parts)
    except Exception as e:
        logger.warning("Q&A 포트폴리오 조회 실패: {}", str(e))
        portfolio_text = "(포트폴리오 조회 실패)"

    has_portfolio = bool(portfolio_text and "조회 실패" not in portfolio_text)
    context_summary = f"{context_label} — {len(activities)}건의 활동 기록"
    if has_portfolio:
        context_summary += " + 포트폴리오"

    # 3. LLM 호출 (Tier1, 속도 우선)
    from analysis.llm.llm_factory import llm_factory

    system_prompt = (
        "너는 AI 트레이딩 시스템의 운영 어시스턴트다. "
        "아래 포트폴리오 현황과 활동 기록을 바탕으로 사용자의 질문에 간결하고 정확하게 답변해라. "
        "추측하지 말고, 제공된 데이터에 근거한 답변만 해라. "
        "한국어로 답변하되, 핵심만 2-3문단 이내로."
    )
    prompt = (
        f"## 포트폴리오 현황 (실시간)\n{portfolio_text}\n\n"
        f"## 활동 기록 ({context_summary})\n{context_text}\n\n"
        f"## 질문\n{req.question}"
    )

    try:
        answer, provider = await llm_factory.generate_tier1(prompt, system_prompt)
    except Exception as e:
        logger.error("Q&A LLM 호출 실패: {}", str(e))
        answer = f"LLM 호출에 실패했습니다: {str(e)[:100]}"
        provider = "error"

    elapsed_ms = int((_time.time() - start) * 1000)

    # 4. 활동 로그 기록
    await activity_logger.log(
        ActivityType.QA, ActivityPhase.COMPLETE,
        f"Q&A: {req.question[:80]}",
        detail={
            "question": req.question,
            "answer": answer[:1000],
            "context_summary": context_summary,
            "llm_provider": provider,
        },
        execution_time_ms=elapsed_ms,
    )

    return SuccessResponse(data=QAResponse(
        question=req.question,
        answer=answer,
        context_summary=context_summary,
        llm_provider=provider,
        execution_time_ms=elapsed_ms,
    ))
