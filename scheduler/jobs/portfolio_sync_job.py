"""장 마감 후 포트폴리오 정산"""
from loguru import logger


async def portfolio_sync_job() -> None:
    """KIS 계좌와 포트폴리오 DB 동기화"""
    logger.info("포트폴리오 정산 시작")
    # Phase 6에서 구현
    # - MCP로 KIS 계좌 잔고/보유종목 조회
    # - DB의 포트폴리오 데이터와 동기화
    # - 미체결 주문 상태 업데이트
    logger.info("포트폴리오 정산 완료")
