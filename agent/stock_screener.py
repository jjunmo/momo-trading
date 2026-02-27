"""AI가 분석 가치 판단 → 후보 종목 선정"""
import json

from loguru import logger

from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.market_scan import STOCK_SCREENING_PROMPT
from services.activity_logger import activity_logger


class StockScreener:
    """
    시장 스캔 결과를 AI에게 전달하여 분석 가치가 있는 종목을 선별.
    """

    async def screen(
        self,
        scan_result: dict,
        active_strategies: list[str] | None = None,
        holding_count: int = 0,
        recent_analyses: list[str] | None = None,
        cycle_id: str | None = None,
    ) -> list[dict]:
        """후보 종목 중 실제 분석/매매할 종목 최종 선정"""
        candidates = scan_result.get("candidates", [])
        if not candidates:
            logger.info("스크리닝: 후보 종목 없음")
            return []

        timer = activity_logger.timer()
        await activity_logger.log(
            "SCREENING", "START",
            f"\U0001f50d AI 스크리닝 시작: {len(candidates)}종목 분석 중",
            cycle_id=cycle_id,
        )

        prompt = STOCK_SCREENING_PROMPT.format(
            candidates_data=json.dumps(candidates, ensure_ascii=False, indent=2),
            active_strategies=", ".join(active_strategies or ["STABLE_SHORT", "AGGRESSIVE_SHORT"]),
            holding_count=holding_count,
            recent_analyses=", ".join(recent_analyses or ["없음"]),
            market_regime=scan_result.get("market_regime", "N/A"),
        )

        try:
            result_text, provider = await llm_factory.generate_tier1(prompt)
            parsed = self._parse_json_response(result_text)
            selected = parsed.get("selected", [])
            elapsed = activity_logger.elapsed_ms(timer)

            logger.info(
                "종목 스크리닝 완료 ({}): {}개 → {}개 선정",
                provider, len(candidates), len(selected)
            )

            selected_lines = []
            for s in selected:
                name = s.get("name", s.get("symbol", "?"))
                reason = s.get("reason", "")
                strategy = s.get("strategy_type", "")
                line = f"  {name} [{strategy}]"
                if reason:
                    line += f" — {reason}"
                selected_lines.append(line)

            summary_text = f"\U0001f50d AI 스크리닝: {len(candidates)}종목 → {len(selected)}종목 선별"
            if selected_lines:
                summary_text += "\n" + "\n".join(selected_lines)

            await activity_logger.log(
                "SCREENING", "COMPLETE",
                summary_text,
                cycle_id=cycle_id,
                detail={
                    "input_count": len(candidates),
                    "selected_count": len(selected),
                    "selected": selected,
                },
                llm_provider=provider,
                llm_tier="TIER1",
                execution_time_ms=elapsed,
            )
            return selected
        except Exception as e:
            elapsed = activity_logger.elapsed_ms(timer)
            logger.error("종목 스크리닝 실패: {}", str(e))
            await activity_logger.log(
                "SCREENING", "ERROR",
                f"\u274c 스크리닝 실패, 상위 3종목 Fallback: {str(e)[:80]}",
                cycle_id=cycle_id,
                error_message=str(e),
                execution_time_ms=elapsed,
            )
            # Fallback: 후보 중 상위 3개 그대로 반환
            return [
                {"symbol": c["symbol"], "name": c.get("name", ""), "strategy_type": "STABLE_SHORT"}
                for c in candidates[:3]
            ]

    def _parse_json_response(self, text: str) -> dict:
        from core.json_utils import parse_llm_json
        return parse_llm_json(text)


stock_screener = StockScreener()
