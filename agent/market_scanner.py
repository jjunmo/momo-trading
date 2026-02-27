"""시장 스캔 + 종목 선별 통합 — MCP 데이터 병렬 수집 → AI 한 번에 분석+선별"""
import asyncio

from loguru import logger

from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.market_scan import MARKET_SCAN_PROMPT, MARKET_SCAN_SYSTEM
from core.database import AsyncSessionLocal
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.mcp_client import mcp_client


class MarketScanner:
    """
    MCP를 통해 시장 데이터 수집 → AI가 시장 국면 판단 + 최종 종목 선별을 한 번에 수행.
    (기존 scan → screening 2단계를 1단계로 통합하여 LLM 호출 1건 절약)
    """

    async def scan(self, cycle_id: str | None = None) -> dict:
        """시장 스캔 + 종목 선별 통합 실행"""
        logger.info("시장 스캔 시작")
        timer = activity_logger.timer()

        await activity_logger.log(
            "SCAN", "START",
            "\U0001f4e1 시장 스캔 중... 거래량/등락 상위 종목 조회",
            cycle_id=cycle_id,
        )

        # 1. 데이터 수집 병렬화 (MCP 4건 + DB 1건 + 계좌 2건)
        (
            available_cash,
            volume_rank,
            surge_data,
            drop_data,
            holdings,
            performance_summary,
        ) = await asyncio.gather(
            account_manager.get_available_cash(),
            self._get_volume_rank(),
            self._get_fluctuation_rank("top"),
            self._get_fluctuation_rank("bottom"),
            account_manager.get_holdings(),
            self._get_performance_summary(),
        )
        max_per_stock = available_cash * 0.2

        data_elapsed = activity_logger.elapsed_ms(timer)
        logger.info("MCP 데이터 수집 완료: {}ms", data_elapsed)

        # 2. AI 시장 분석 + 종목 선별 (통합 1회 호출)
        from util.time_util import now_kst
        from core.config import settings as _settings

        now = now_kst()
        cutoff_time = now.replace(
            hour=_settings.BUY_CUTOFF_HOUR,
            minute=_settings.BUY_CUTOFF_MINUTE,
            second=0, microsecond=0,
        )
        minutes_until_cutoff = max(0, int((cutoff_time - now).total_seconds() / 60))

        prompt = MARKET_SCAN_PROMPT.format(
            current_time=now.strftime("%H:%M"),
            minutes_until_cutoff=minutes_until_cutoff,
            available_cash=available_cash,
            max_per_stock=max_per_stock,
            volume_rank_data=self._format_data(volume_rank),
            surge_data=self._format_data(surge_data),
            drop_data=self._format_data(drop_data),
            holdings_data=self._format_holdings(holdings),
            holding_count=len(holdings),
            performance_summary=performance_summary,
        )

        try:
            result_text, provider = await llm_factory.generate_tier1(
                prompt, system_prompt=MARKET_SCAN_SYSTEM
            )
            parsed = self._parse_json_response(result_text)
            selected = parsed.get("selected", [])
            elapsed = activity_logger.elapsed_ms(timer)

            logger.info(
                "시장 스캔+선별 완료 ({}): {}개 선정 (데이터 {}ms + AI {}ms)",
                provider, len(selected), data_elapsed, elapsed - data_elapsed,
            )

            # 활동 로그 요약
            selected_lines = []
            for s in selected[:8]:
                name = s.get("name", s.get("symbol", "?"))
                strategy = s.get("strategy_type", "")
                reason = s.get("reason", "")
                line = f"  {name} [{strategy}]"
                if reason:
                    line += f" — {reason}"
                selected_lines.append(line)

            summary_text = f"\U0001f4e1 시장 스캔 완료: {len(selected)}개 선정"
            if selected_lines:
                summary_text += "\n" + "\n".join(selected_lines)
            market_analysis = parsed.get("market_analysis", "")
            if market_analysis:
                summary_text += f"\n   시장: {market_analysis}"

            await activity_logger.log(
                "SCAN", "COMPLETE",
                summary_text,
                cycle_id=cycle_id,
                detail={
                    "selected_count": len(selected),
                    "selected": selected,
                    "market_regime": parsed.get("market_regime", ""),
                    "market_analysis": market_analysis,
                    "available_cash": available_cash,
                },
                llm_provider=provider,
                llm_tier="TIER1",
                execution_time_ms=elapsed,
            )

            return {
                "selected": selected,
                "market_summary": parsed.get("market_analysis", ""),
                "market_regime": parsed.get("market_regime", ""),
                "market_analysis": parsed.get("market_analysis", ""),
                "leading_sectors": parsed.get("leading_sectors", []),
                "available_cash": available_cash,
                "max_per_stock": max_per_stock,
                "provider": provider,
            }
        except Exception as e:
            elapsed = activity_logger.elapsed_ms(timer)
            err_msg = str(e) or repr(e)
            logger.error("시장 스캔 AI 분석 실패 ({}): {}", type(e).__name__, err_msg)
            await activity_logger.log(
                "SCAN", "ERROR",
                f"\u274c 시장 스캔 실패: [{type(e).__name__}] {err_msg[:100]}",
                cycle_id=cycle_id,
                error_message=err_msg,
                execution_time_ms=elapsed,
            )
            return {"selected": [], "market_summary": "스캔 실패", "available_cash": available_cash}

    async def _get_performance_summary(self) -> str:
        """과거 매매 성과 요약 텍스트 생성"""
        try:
            async with AsyncSessionLocal() as session:
                tracker = PerformanceTracker(session)
                stats = await tracker.get_overall_stats()

            overall = stats.get("overall")
            if not overall or overall.total_trades == 0:
                return "매매 이력 없음"

            lines = [
                f"총 {overall.total_trades}거래, "
                f"승률 {overall.win_rate * 100:.1f}%, "
                f"총손익 {overall.total_pnl:+,.0f}원, "
                f"평균수익률 {overall.avg_return:+.2f}%"
            ]

            by_strategy = stats.get("by_strategy", {})
            for strategy_type, stat in by_strategy.items():
                lines.append(
                    f"  - {strategy_type}: {stat.total_trades}거래, "
                    f"승률 {stat.win_rate * 100:.1f}%, "
                    f"평균수익률 {stat.avg_return:+.2f}%"
                )

            return "\n".join(lines)
        except Exception as e:
            logger.warning("성과 요약 조회 실패: {}", str(e))
            return "매매 이력 없음"

    async def _get_volume_rank(self) -> list[dict]:
        resp = await mcp_client.get_volume_rank()
        if resp.success and resp.data:
            return resp.data.get("stocks", resp.data.get("items", []))
        return []

    async def _get_fluctuation_rank(self, sort: str) -> list[dict]:
        resp = await mcp_client.get_fluctuation_rank(sort=sort)
        if resp.success and resp.data:
            return resp.data.get("stocks", resp.data.get("items", []))
        return []

    def _format_data(self, data: list[dict]) -> str:
        if not data:
            return "데이터 없음"
        lines = []
        for i, item in enumerate(data[:15], 1):
            symbol = item.get("symbol", item.get("code", ""))
            name = item.get("name", "")
            price = item.get("price", item.get("current_price", ""))
            change_rate = item.get("change_rate", "")
            volume = item.get("volume", "")
            lines.append(f"{i}. {name}({symbol}) {price}원 {change_rate}% 거래량:{volume}")
        return "\n".join(lines)

    def _format_holdings(self, holdings) -> str:
        if not holdings:
            return "보유 종목 없음"
        lines = []
        for h in holdings:
            lines.append(
                f"- {h.name}({h.symbol}) {h.quantity}주 "
                f"평균단가:{h.avg_buy_price:,.0f} 수익률:{h.pnl_rate:+.2f}%"
            )
        return "\n".join(lines)

    def _parse_json_response(self, text: str) -> dict:
        from core.json_utils import parse_llm_json
        return parse_llm_json(text)


market_scanner = MarketScanner()
