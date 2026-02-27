"""AI 자율 한도 결정 - 계좌 상태 + 성과 + 리스크 성향 기반"""
import json

from loguru import logger

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


class AIRiskTuner:
    """AI가 계좌 상태 + 성과 + 리스크 성향을 분석하여 한도를 자율 결정"""

    async def compute_limits(
        self,
        risk_appetite: str = "MODERATE",
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

            # 3. 리스크 성향 가이드라인
            risk_guideline = RISK_APPETITE_GUIDELINES.get(
                risk_appetite, RISK_APPETITE_GUIDELINES["MODERATE"]
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

            await activity_logger.log(
                "RISK_TUNING", "COMPLETE",
                f"\U0001f3af AI 한도 결정 ({risk_appetite}): "
                f"일일거래 {limits['max_daily_trades']}회, "
                f"주문한도 {limits['max_single_order_krw']:,.0f}원",
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
                int(parsed.get("max_daily_trades", settings.MAX_DAILY_TRADES)), 1
            ),
            "max_single_order_krw": max(
                int(parsed.get("max_single_order_krw", 10_000_000)), 100_000
            ),
            "min_buy_quantity": max(
                int(parsed.get("min_buy_quantity", settings.MIN_BUY_QUANTITY)), 1
            ),
            "max_position_pct": max(
                min(float(parsed.get("max_position_pct", 20.0)), 50.0), 5.0
            ),
            "min_cash_ratio": max(
                float(parsed.get("min_cash_ratio", 0.2)), 0.1
            ),
            "reasoning": parsed.get("reasoning", ""),
        }

    def _default_limits(self) -> dict:
        """기본 한도값 (AI 실패 시 — 보수적 기본값)"""
        return {
            "max_daily_trades": settings.MAX_DAILY_TRADES,
            "max_single_order_krw": 5_000_000,
            "min_buy_quantity": settings.MIN_BUY_QUANTITY,
            "max_position_pct": 15.0,
            "min_cash_ratio": 0.3,
            "reasoning": "AI 한도 결정 실패, 보수적 기본값 사용",
        }

    def _parse_json(self, text: str) -> dict | None:
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
        return None


ai_risk_tuner = AIRiskTuner()
