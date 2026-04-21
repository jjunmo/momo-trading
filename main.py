from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from api.router import api_router
from core.config import settings
from core.events import event_bus
from core.logging import setup_logging
from exceptions.common import ServiceException
from middleware.request_id import RequestIDMiddleware
from models import Base  # noqa: F401 - 모든 모델 import하여 metadata에 등록
from schemas.common import BasicErrorResponse
from trading.mcp_client import mcp_client
from util.time_util import now_kst


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings.validate_on_startup()
    # DB 스키마는 Alembic으로 관리: python -m alembic upgrade head
    logger.info("애플리케이션 시작 (ENVIRONMENT={})", settings.ENVIRONMENT)

    # 이벤트 버스 시작
    await event_bus.start()

    # MCP 클라이언트 연결 (실패해도 서버는 기동)
    try:
        await mcp_client.connect()
        tools = await mcp_client.list_tools()
        logger.info("MCP 도구 목록 ({}개): {}", len(tools), [t.get("name") for t in tools])
    except Exception as e:
        logger.warning("MCP 서버 연결 실패 (나중에 재시도): {}", str(e))

    # event_detector 임계값 복원 (DB의 open BUY 포지션 → 메모리)
    try:
        from realtime.event_detector import event_detector
        from repositories.trade_result_repository import TradeResultRepository
        from core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            repo = TradeResultRepository(session)
            open_positions = await repo.get_all_open()
        restored = 0
        for tr in open_positions:
            if tr.side != "BUY":
                continue
            kwargs = {}
            if tr.ai_target_price and tr.ai_target_price > 0:
                kwargs["take_profit"] = tr.ai_target_price
                kwargs["initial_take_profit"] = tr.ai_target_price
            if tr.ai_stop_loss_price and tr.ai_stop_loss_price > 0:
                kwargs["stop_loss"] = tr.ai_stop_loss_price
                kwargs["initial_stop_loss"] = tr.ai_stop_loss_price
            if tr.entry_price and tr.entry_price > 0:
                kwargs["entry_price"] = tr.entry_price
            if kwargs:
                event_detector.set_thresholds(tr.stock_symbol, **kwargs)
                restored += 1
        logger.info("event_detector 임계값 복원: {}건 (open BUY {}건)", restored, len(open_positions))
    except Exception as e:
        logger.warning("event_detector 임계값 복원 실패: {}", str(e))

    # 매매불가 블록리스트 복원 (DB → 메모리)
    try:
        from agent.market_scanner import market_scanner
        await market_scanner.load_untradeable_from_db()
    except Exception as e:
        logger.warning("매매불가 블록리스트 복원 실패: {}", str(e))

    # 실시간 모니터 시작 (WebSocket, 실패해도 서버 기동)
    from realtime.monitor import realtime_monitor
    try:
        await realtime_monitor.start()
    except Exception as e:
        logger.warning("실시간 모니터 시작 실패: {}", str(e))

    # AI Trading Agent 시작
    from agent.trading_agent import trading_agent
    try:
        await trading_agent.start()
    except Exception as e:
        logger.warning("Trading Agent 시작 실패: {}", str(e))

    # 스케줄러 시작
    from scheduler.scheduler import trading_scheduler
    try:
        await trading_scheduler.start()
    except Exception as e:
        logger.warning("스케줄러 시작 실패: {}", str(e))

    logger.info("모든 서브시스템 초기화 완료")

    yield

    # 종료 처리 (역순)
    try:
        await trading_scheduler.stop()
    except Exception:
        pass
    try:
        await trading_agent.stop()
    except Exception:
        pass
    try:
        await realtime_monitor.stop()
    except Exception:
        pass
    await mcp_client.disconnect()
    await event_bus.stop()
    logger.info("애플리케이션 종료")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_local else None,
    redoc_url="/redoc" if settings.is_local else None,
)

# ── 미들웨어 ──
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 라우터 ──
app.include_router(api_router)

# ── Admin 정적 파일 서빙 ──
ADMIN_STATIC = Path(__file__).parent / "admin" / "static"
app.mount("/admin/static", StaticFiles(directory=str(ADMIN_STATIC)), name="admin-static")


@app.get("/admin")
@app.get("/admin/")
async def admin_dashboard():
    """관리자 대시보드 UI"""
    return FileResponse(str(ADMIN_STATIC / "index.html"))


# ── 예외 핸들러 ──
@app.exception_handler(ServiceException)
async def service_exception_handler(request: Request, exc: ServiceException):
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "서비스 예외 발생: {} - {} (request_id={})",
        exc.error_code,
        exc.message,
        request_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=BasicErrorResponse(
            errorCode=exc.error_code,
            message=exc.message,
            data=exc.data,
            timestamp=now_kst(),
            request_id=request_id,
            path=request.url.path,
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "처리되지 않은 예외 (request_id={}): {}", request_id, str(exc)
    )
    return JSONResponse(
        status_code=500,
        content=BasicErrorResponse(
            errorCode="INTERNAL_SERVER_ERROR",
            message="서버 내부 오류가 발생했습니다",
            timestamp=now_kst(),
            request_id=request_id,
            path=request.url.path,
        ).model_dump(mode="json"),
    )
