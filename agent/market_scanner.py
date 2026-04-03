"""시장 스캔 + 종목 선별 통합 — MCP 데이터 병렬 수집 → AI 한 번에 분석+선별"""
import asyncio

from loguru import logger

from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.market_scan import MARKET_SCAN_PROMPT, MARKET_SCAN_SYSTEM
from core.database import AsyncSessionLocal
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType
from trading.mcp_client import mcp_client

# 모의투자 매매불가 종목 필터 키워드
_EXCLUDE_NAME_KEYWORDS = ("ETN", "스팩", "SPAC")


class MarketScanner:
    """
    MCP를 통해 시장 데이터 수집 → AI가 시장 국면 판단 + 최종 종목 선별을 한 번에 수행.
    (기존 scan → screening 2단계를 1단계로 통합하여 LLM 호출 1건 절약)
    """

    def __init__(self):
        self._untradeable_symbols: set[str] = set()

    def add_untradeable(self, symbol: str) -> None:
        """매매불가 종목을 런타임 블록리스트에 등록 (당일 스캔에서 제외)"""
        self._untradeable_symbols.add(symbol)
        logger.debug("매매불가 블록리스트 등록: {} (총 {}건)", symbol, len(self._untradeable_symbols))

    def _filter_untradeable(self, stocks: list[dict]) -> list[dict]:
        """매매불가 종목 필터링 (런타임 블록리스트 + 이름 키워드)"""
        filtered = []
        for s in stocks:
            name = s.get("name", "")
            symbol = s.get("symbol", "")
            if symbol in self._untradeable_symbols:
                continue
            if any(kw in name for kw in _EXCLUDE_NAME_KEYWORDS):
                continue
            filtered.append(s)
        if len(filtered) < len(stocks):
            logger.debug("매매불가 종목 필터: {}건 → {}건", len(stocks), len(filtered))
        return filtered

    def _build_price_lookup(self, *data_lists: list[dict]) -> dict[str, float]:
        """스캔 데이터에서 종목코드→현재가 매핑"""
        lookup: dict[str, float] = {}
        for data in data_lists:
            for item in data:
                sym = item.get("symbol", item.get("code", ""))
                if not sym:
                    continue
                raw = item.get("price", item.get("current_price", 0))
                try:
                    price = float(str(raw).replace(",", ""))
                    if price > 0:
                        lookup[sym] = price
                except (ValueError, TypeError):
                    continue
        return lookup

    async def scan(self, cycle_id: str | None = None, dynamic_limits: dict | None = None) -> dict:
        """시장 스캔 + 종목 선별 통합 실행"""
        logger.debug("시장 스캔 시작")
        timer = activity_logger.timer()

        await activity_logger.log(
            ActivityType.SCAN, ActivityPhase.START,
            "\U0001f4e1 시장 스캔 중... 거래량/등락 상위 종목 조회",
            cycle_id=cycle_id,
        )

        # 1. 데이터 수집 병렬화 (MCP 3건 + DB 1건 + 계좌 1건 + 지수 2건)
        from trading.kis_api import get_market_index
        (
            account_snapshot,
            volume_rank,
            surge_data,
            drop_data,
            performance_summary,
            kospi_index,
            kosdaq_index,
        ) = await asyncio.gather(
            account_manager.get_account_snapshot(),
            self._get_volume_rank(),
            self._get_fluctuation_rank("top"),
            self._get_fluctuation_rank("bottom"),
            self._get_performance_summary(),
            get_market_index("0001"),
            get_market_index("2001"),
        )
        balance, holdings = account_snapshot
        available_cash = balance.cash
        total_asset = balance.total_asset or available_cash
        max_pos_pct = 0.2
        if dynamic_limits:
            max_pos_pct = dynamic_limits.get("max_position_pct", 20.0) / 100
        max_per_stock = available_cash * max_pos_pct

        # 현금 비율 매우 낮으면 보유종목 매도 검토 힌트
        rotation_hint = ""
        if total_asset > 0 and available_cash < total_asset * 0.1 and len(holdings) > 0:
            rotation_hint = "⚠️ 현금 비율 매우 낮음 — 보유종목 중 정체/부진 종목 매도 검토 필요"

        data_elapsed = activity_logger.elapsed_ms(timer)
        logger.debug("MCP 데이터 수집 완료: {}ms", data_elapsed)

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

        # 시장 지수 포맷
        index_lines = []
        for idx in [kospi_index, kosdaq_index]:
            if idx.get("success"):
                vol_str = f"{idx['volume'] / 1_0000:,.0f}만주" if idx.get("volume") else ""
                index_lines.append(
                    f"{idx['name']}: {idx['price']:,.2f} ({idx['change_rate']:+.2f}%) {vol_str}"
                )
        market_index_data = "\n".join(index_lines) if index_lines else "지수 데이터 없음"

        prompt = MARKET_SCAN_PROMPT.format(
            current_time=now.strftime("%H:%M"),
            minutes_until_cutoff=minutes_until_cutoff,
            total_asset=total_asset,
            available_cash=available_cash,
            max_per_stock=max_per_stock,
            rotation_hint=rotation_hint,
            market_index_data=market_index_data,
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

            # 가격 기반 사전 필터: 1주 매수 불가능한 종목 제거
            if available_cash > 0 and selected:
                price_lookup = self._build_price_lookup(volume_rank, surge_data, drop_data)
                before = len(selected)
                selected = [
                    s for s in selected
                    if s.get("direction") != "BUY"
                    or price_lookup.get(s.get("symbol", ""), 0) <= 0
                    or price_lookup[s["symbol"]] <= available_cash
                ]
                if len(selected) < before:
                    logger.debug("현금 필터: {}건 → {}건 (가용 {:,.0f}원)", before, len(selected), available_cash)

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
                ActivityType.SCAN, ActivityPhase.COMPLETE,
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
                ActivityType.SCAN, ActivityPhase.ERROR,
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
            stocks = resp.data.get("stocks", resp.data.get("items", []))
            return self._filter_untradeable(stocks)
        return []

    async def _get_fluctuation_rank(self, sort: str) -> list[dict]:
        resp = await mcp_client.get_fluctuation_rank(sort=sort)
        if resp.success and resp.data:
            stocks = resp.data.get("stocks", resp.data.get("items", []))
            return self._filter_untradeable(stocks)
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
