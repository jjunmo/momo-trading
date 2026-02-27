"""일봉 데이터 보관용 수집"""
from loguru import logger


async def market_data_job() -> None:
    """일봉 데이터 수집 및 보관"""
    logger.info("일봉 데이터 수집 시작")
    # Phase 6에서 구현
    # - 보유 종목 + 관심 종목의 일봉 데이터 수집
    # - MCP로 일봉 데이터 조회
    # - DB에 저장 (MarketDataDaily)
    logger.info("일봉 데이터 수집 완료")
