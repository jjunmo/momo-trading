"""AI 자율 한도 결정 - 계좌 상태 + 성과 + 리스크 성향 기반"""
from loguru import logger

from core.json_utils import parse_llm_json

from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.risk_tuning import (
    RISK_APPETITE_GUIDELINES,
    RISK_TUNING_PROMPT,
    RISK_TUNING_SYSTEM,
)
from core.config import settings
from core.database import AsyncSessionLocal
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType


class AIRiskTuner:
    """AI가 계좌 상태 + 성과 + 리스크 성향을 분석하여 한도를 자율 결정"""

    async def compute_limits(
        self,
        risk_appetite: str = "",  # deprecated: AI가 자율 판단
        cycle_id: str | None = None,
    ) -> dict:
        """적정 한도 계산"""
        timer = activity_logger.timer()

        try:
            # 1. 계좌 잔고 조회
            balance = await account_manager.get_balance()

            # 2. 최근 매매 성과 조회
            performance_summary = "매매 이력 없음"
            try:
                async with AsyncSessionLocal() as session:
                    tracker = PerformanceTracker(session)
                    stats = await tracker.get_overall_stats()
                    overall = stats.get("overall")
                    if overall and overall.total_trades > 0:
                        performance_summary = (
                            f"총 {overall.total_trades}거래, "
                            f"승률 {overall.win_rate * 100:.1f}%, "
                            f"총손익 {overall.total_pnl:+,.0f}원, "
                            f"평균수익률 {overall.avg_return:+.2f}%"
                        )
            except Exception as e:
                logger.warning("성과 데이터 조회 실패: {}", str(e))

            # 3. 리스크 성향: AI가 시장 국면 + 성과 보고 자율 판단
            from agent.market_regime_agent import market_regime_agent
            regime = market_regime_agent.current_regime or "정보 없음"
            risk_guideline = (
                f"[AI 자율 판단]\n"
                f"현재 시장 국면: {regime}\n"
                f"계좌 상태와 매매 성과, 시장 국면을 종합하여 리스크 성향을 자율적으로 판단하세요.\n"
                f"보수적/중립/공격적 중 현 상황에 가장 적합한 성향을 선택하고 그에 맞는 한도를 설정하세요."
            )

            # 4. 현금 비율 계산
            cash_ratio = 0.0
            if balance.total_asset > 0:
                cash_ratio = (balance.cash / balance.total_asset) * 100

            # 5. LLM에게 한도 요청
            prompt = RISK_TUNING_PROMPT.format(
                total_asset=balance.total_asset,
                cash=balance.cash,
                stock_value=balance.stock_value,
                cash_ratio=cash_ratio,
                total_pnl=balance.total_pnl,
                total_pnl_rate=balance.total_pnl_rate,
                performance_summary=performance_summary,
                risk_guideline=risk_guideline,
                max_daily_trades=settings.MAX_DAILY_TRADES,
                min_buy_quantity=settings.MIN_BUY_QUANTITY,
            )

            result_text, provider = await llm_factory.generate_tier1(
                prompt, system_prompt=RISK_TUNING_SYSTEM
            )

            # 6. 파싱 + clamp (settings 상한선으로 제한)
            parsed = self._parse_json(result_text)
            if not parsed:
                logger.warning("AI 한도 파싱 실패, 기본값 사용")
                return self._default_limits()

            limits = self._clamp_limits(parsed)
            elapsed = activity_logger.elapsed_ms(timer)

            reasoning = limits.get("reasoning", "")
            reasoning_short = reasoning[:100] if reasoning else ""
            await activity_logger.log(
                ActivityType.RISK_TUNING, ActivityPhase.COMPLETE,
                f"\U0001f3af AI 한도 결정 ({risk_appetite}): "
                f"일일거래 {limits['max_daily_trades']}회, "
                f"주문한도 {limits['max_single_order_krw']:,.0f}원, "
                f"포지션 {limits['max_position_pct']:.0f}%"
                f"{f' — {reasoning_short}' if reasoning_short else ''}",
                cycle_id=cycle_id,
                detail=limits,
                llm_provider=provider,
                llm_tier="TIER1",
                execution_time_ms=elapsed,
            )

            return limits

        except Exception as e:
            logger.error("AI 한도 결정 실패: {}", str(e))
            return self._default_limits()

    def _clamp_limits(self, parsed: dict) -> dict:
        """AI 결정값 정규화 (최소 안전값만 적용, 상한선 없음)"""
        return {
            "max_daily_trades": max(
                int(parsed.get("max_daily_trades", settings.MAX_DAILY_TRADES)), 0
            ),  # 0 = 무제한
            "max_single_order_krw": max(
                int(parsed.get("max_single_order_krw", 0)), 0
            ),  # 0 = 무제한
            "min_buy_quantity": max(
                int(parsed.get("min_buy_quantity", settings.MIN_BUY_QUANTITY)), 1
            ),
            "max_position_pct": max(
                float(parsed.get("max_position_pct", 25.0)), 5.0
            ),  # 상한선 없음
            "min_cash_ratio": max(
                float(parsed.get("min_cash_ratio", 0.0)), 0.0
            ),  # 0 = 제한 없음
            "reasoning": parsed.get("reasoning", ""),
        }

    def _default_limits(self) -> dict:
        """기본 한도값 (AI 실패 시)"""
        return {
            "max_daily_trades": settings.MAX_DAILY_TRADES,  # 0 = 무제한
            "max_single_order_krw": settings.MAX_SINGLE_ORDER_KRW,  # 0 = 무제한
            "min_buy_quantity": settings.MIN_BUY_QUANTITY,
            "max_position_pct": 25.0,
            "min_cash_ratio": 0.0,
            "reasoning": "AI 한도 결정 실패, 기본값 사용",
        }

    def _parse_json(self, text: str) -> dict | None:
        result = parse_llm_json(text)
        return result or None


ai_risk_tuner = AIRiskTuner()
