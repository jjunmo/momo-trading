from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from api.router import api_router
from core.config import settings
from core.database import engine
from core.logging import setup_logging
from exceptions.common import ServiceException
from middleware.request_id import RequestIDMiddleware
from models.base import Base
from schemas.common import BasicErrorResponse
from util.time_util import now_kst


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    Base.metadata.create_all(bind=engine)
    logger.info("애플리케이션 시작 (ENVIRONMENT={})", settings.ENVIRONMENT)
    yield
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
