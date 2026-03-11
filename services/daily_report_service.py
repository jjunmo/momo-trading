"""일일 리포트 서비스 — 활동 집계 + 계좌 스냅샷 + LLM 요약 생성"""
import json
from datetime import date

from loguru import logger

from analysis.llm.llm_factory import llm_factory
from core.json_utils import parse_llm_json
from core.database import AsyncSessionLocal
from models.daily_report import DailyReport
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository
from repositories.trade_result_repository import TradeResultRepository
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType

DAILY_REPORT_PROMPT = """당신은 AI 트레이딩 시스템의 일일 리포트 작성자입니다.
오늘 하루의 활동 데이터를 기반으로 다음 항목을 한국어로 작성해주세요.

## 오늘 날짜: {report_date}

## 활동 요약
- 사이클 수: {total_cycles}
- 분석 건수: {total_analyses}
- 추천 건수: {total_recommendations}
- 오늘 매수: {buy_count}건 / 매도: {sell_count}건
- 청산 승/패: {win_count}/{loss_count}
- 실현 손익: {total_pnl:+,.0f}원
- 미실현 손익: {unrealized_pnl:+,.0f}원 (보유 {open_position_count}종목)

## 계좌 현황
- 총자산: {total_asset:,.0f}원
- 현금: {cash:,.0f}원
- 주식 평가: {stock_value:,.0f}원

## 활동 타입별 집계
{activity_counts}

## 주요 활동 로그 (최근)
{recent_activities}

다음 JSON 형식으로 응답해주세요:
{{
  "market_summary": "오늘 시장 상황 요약 (2-3문장)",
  "performance_review": "성과 분석 (좋았던 점, 아쉬운 점)",
  "lessons_learned": "오늘 학습한 점 (패턴, 전략 개선점)",
  "next_day_plan": "내일 전략과 관심 종목",
  "top_picks": ["종목1", "종목2"]
}}"""


class DailyReportService:
    """일일 리포트 생성 서비스 (자체 세션 사용)"""

    async def generate_daily_report(self, report_date: date | None = None) -> DailyReport | None:
        """일일 리포트 생성"""
        from util.time_util import now_kst
        if report_date is None:
            report_date = now_kst().date()

        logger.info("일일 리포트 생성 시작: {}", report_date)

        await activity_logger.log(
            ActivityType.REPORT, ActivityPhase.START,
            f"\U0001f4cb 일일 리포트 생성 시작: {report_date}",
        )

        try:
            # 계좌 스냅샷 조회 (세션 밖에서 — MCP 호출)
            unrealized_pnl = 0.0
            open_position_count = 0
            total_asset = 0.0
            cash = 0.0
            stock_value = 0.0
            try:
                balance, holdings = await account_manager.get_account_snapshot()
                unrealized_pnl = balance.total_pnl
                open_position_count = len(holdings)
                total_asset = balance.total_asset
                cash = balance.cash
                stock_value = balance.stock_value
            except Exception as e:
                logger.warning("계좌 스냅샷 조회 실패 (리포트 계속): {}", str(e))

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    activity_repo = AgentActivityRepository(session)
                    report_repo = DailyReportRepository(session)

                    # 기존 리포트 확인 (중복 방지)
                    existing = await report_repo.get_by_date(report_date)
                    if existing:
                        logger.info("이미 리포트 존재: {}", report_date)
                        return existing

                    # 활동 데이터 집계
                    activities = await activity_repo.get_by_date(report_date, limit=500)
                    activity_counts = await activity_repo.count_by_date(report_date)

                    # 기본 통계
                    total_cycles = activity_counts.get("CYCLE", 0) // 2  # START + COMPLETE
                    total_analyses = activity_counts.get("TIER1_ANALYSIS", 0)
                    total_recommendations = activity_counts.get("DECISION", 0)

                    # TradeResult 기반 매수/매도/승패/손익 집계
                    trade_result_repo = TradeResultRepository(session)
                    opened_trades = await trade_result_repo.get_opened_by_date(report_date)
                    completed_trades = await trade_result_repo.get_completed_by_date(report_date)

                    buy_count = len(opened_trades)
                    sell_count = len(completed_trades)
                    total_orders = buy_count + sell_count
                    win_count = sum(1 for t in completed_trades if t.is_win)
                    loss_count = sum(1 for t in completed_trades if not t.is_win)
                    total_pnl = sum(t.pnl for t in completed_trades)

                    # DB에 미청산 포지션이 없으면 계좌 보유 종목 수 사용
                    if open_position_count == 0:
                        all_open = await trade_result_repo.get_all_open()
                        if all_open:
                            open_position_count = len(all_open)

                    # LLM으로 리포트 생성
                    recent_summaries = "\n".join(
                        f"[{a.activity_type}] {a.summary}" for a in activities[-30:]
                    )
                    activity_count_text = "\n".join(
                        f"- {k}: {v}건" for k, v in activity_counts.items()
                    )

                    report = DailyReport(
                        report_date=report_date,
                        total_cycles=total_cycles,
                        total_analyses=total_analyses,
                        total_recommendations=total_recommendations,
                        total_orders=total_orders,
                        buy_count=buy_count,
                        sell_count=sell_count,
                        win_count=win_count,
                        loss_count=loss_count,
                        total_pnl=total_pnl,
                        unrealized_pnl=unrealized_pnl,
                        open_position_count=open_position_count,
                    )

                    # LLM 요약 생성 시도
                    try:
                        prompt = DAILY_REPORT_PROMPT.format(
                            report_date=report_date,
                            total_cycles=total_cycles,
                            total_analyses=total_analyses,
                            total_recommendations=total_recommendations,
                            buy_count=buy_count,
                            sell_count=sell_count,
                            win_count=win_count,
                            loss_count=loss_count,
                            total_pnl=total_pnl,
                            unrealized_pnl=unrealized_pnl,
                            open_position_count=open_position_count,
                            total_asset=total_asset,
                            cash=cash,
                            stock_value=stock_value,
                            activity_counts=activity_count_text or "활동 없음",
                            recent_activities=recent_summaries or "활동 없음",
                        )
                        result_text, provider = await llm_factory.generate_tier1(prompt)
                        parsed = self._parse_json(result_text)

                        if parsed:
                            report.market_summary = parsed.get("market_summary", "")
                            report.performance_review = parsed.get("performance_review", "")
                            report.lessons_learned = parsed.get("lessons_learned", "")
                            report.next_day_plan = parsed.get("next_day_plan", "")
                            report.top_picks = json.dumps(
                                parsed.get("top_picks", []), ensure_ascii=False
                            )
                    except Exception as e:
                        logger.warning("LLM 리포트 요약 생성 실패: {}", str(e))
                        report.market_summary = "LLM 요약 생성 실패"
                        report.performance_review = f"활동 {len(activities)}건 기록됨"

                    report.strategy_stats = json.dumps(activity_counts, ensure_ascii=False)
                    session.add(report)

        except Exception as e:
            logger.error("일일 리포트 생성 실패: {}", str(e))
            await activity_logger.log(
                ActivityType.REPORT, ActivityPhase.ERROR,
                f"\u274c 일일 리포트 생성 실패: {str(e)[:100]}",
                error_message=str(e),
            )
            return None

        await activity_logger.log(
            ActivityType.REPORT, ActivityPhase.COMPLETE,
            f"\U0001f4cb 일일 리포트 생성 완료: {report_date}"
            f"\n   사이클 {total_cycles}회 | 매수 {buy_count}건 | 매도 {sell_count}건"
            f"\n   실현 {total_pnl:+,.0f}원 | 미실현 {unrealized_pnl:+,.0f}원",
            detail={
                "report_date": str(report_date),
                "total_cycles": total_cycles,
                "buy_count": buy_count,
                "sell_count": sell_count,
                "total_pnl": total_pnl,
                "unrealized_pnl": unrealized_pnl,
            },
        )

        logger.info("일일 리포트 생성 완료: {}", report_date)
        return report

    def _parse_json(self, text: str) -> dict | None:
        result = parse_llm_json(text)
        return result or None


daily_report_service = DailyReportService()
