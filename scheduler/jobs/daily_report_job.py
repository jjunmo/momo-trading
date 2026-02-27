"""장 마감 후 일일 리포트 생성"""
from loguru import logger


async def daily_report_job() -> None:
    """일일 매매 리포트 생성"""
    logger.info("일일 리포트 생성 시작")
    from services.daily_report_service import daily_report_service
    try:
        report = await daily_report_service.generate_daily_report()
        if report:
            logger.info("일일 리포트 생성 완료: {}", report.report_date)
        else:
            logger.warning("일일 리포트 생성 결과 없음")
    except Exception as e:
        logger.error("일일 리포트 생성 실패: {}", str(e))
