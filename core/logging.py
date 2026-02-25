import logging
import sys

from loguru import logger

from core.config import settings


class InterceptHandler(logging.Handler):
    """표준 logging → loguru 브릿지. uvicorn, SQLAlchemy 등의 로그를 loguru로 통합"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    """앱 시작 시 호출. 환경별 로깅 설정"""
    logger.remove()

    # request_id 기본값 설정 (요청 컨텍스트 밖에서도 에러 없이 동작)
    logger.configure(extra={"request_id": "-"})

    if settings.is_local:
        logger.add(
            sys.stderr,
            level=settings.LOG_LEVEL,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                   "<level>{level: <8}</level> | "
                   "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                   "{extra[request_id]} | "
                   "<level>{message}</level>",
            colorize=True,
            backtrace=True,
            diagnose=True,
        )
    else:
        logger.add(
            sys.stderr,
            level=settings.LOG_LEVEL,
            format="{message}",
            serialize=True,
            backtrace=False,
            diagnose=False,
        )

    # 표준 logging 라이브러리를 loguru로 인터셉트
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"]:
        logging.getLogger(name).handlers = [InterceptHandler()]
