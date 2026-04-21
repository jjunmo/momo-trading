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

    @staticmethod
    def _update_market_breadth(
        volume_rank: list[dict], surge_data: list[dict], drop_data: list[dict]
    ) -> None:
        """스캔 데이터에서 상승/하락 종목수를 계산하여 regime_agent에 전달"""
        seen: set[str] = set()
        advancing = 0
        declining = 0
        for data_list in (volume_rank, surge_data, drop_data):
            for s in data_list:
                sym = s.get("symbol", s.get("code", ""))
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                try:
                    rate = float(str(s.get("change_rate", "0")).replace(",", ""))
                except (ValueError, TypeError):
                    continue
                if rate > 0:
                    advancing += 1
                elif rate < 0:
                    declining += 1
        if advancing + declining > 0:
            from agent.market_regime_agent import market_regime_agent
            market_regime_agent.update_breadth(advancing, declining)
            logger.debug("시장 폭 업데이트: 상승 {}종목 / 하락 {}종목", advancing, declining)

    def _build_market_lookup(self, *data_lists: list[dict]) -> dict[str, str]:
        """스캔 데이터에서 종목코드→시장구분(KRX/NXT) 매핑"""
        lookup: dict[str, str] = {}
        for data in data_lists:
            for item in data:
                sym = item.get("symbol", item.get("code", ""))
                market = item.get("market", "")
                if sym and market and sym not in lookup:
                    lookup[sym] = market
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

        # 시장 폭 계산: 상승/하락 종목수 → regime_agent에 전달
        self._update_market_breadth(volume_rank, surge_data, drop_data)

        # 2. AI 시장 분석 + 종목 선별 (통합 1회 호출)
        from util.time_util import now_kst
        from core.config import settings as _settings

        now = now_kst()
        # 현재 세션 마감까지 남은 시간 (KRX 15:10 / NXT 프리 8:45 / NXT 애프터 19:50)
        from scheduler.market_calendar import market_calendar
        _cutoff = market_calendar.get_trading_cutoff(now)
        cutoff_time = now.replace(hour=_cutoff.hour, minute=_cutoff.minute, second=0, microsecond=0)
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

            # 시장 라벨 lookup (KRX/NXT) — selected 종목에 출처 표기용
            market_lookup = self._build_market_lookup(volume_rank, surge_data, drop_data)

            # 활동 로그 요약 + 각 selected에 market 필드 주입
            selected_lines = []
            for s in selected:
                sym = s.get("symbol", "")
                if sym and "market" not in s:
                    mk = market_lookup.get(sym)
                    if mk:
                        s["market"] = mk
            for s in selected[:8]:
                name = s.get("name", s.get("symbol", "?"))
                strategy = s.get("strategy_type", "")
                reason = s.get("reason", "")
                market = s.get("market", "")
                market_tag = f"[{market}] " if market else ""
                line = f"  {market_tag}{name} [{strategy}]"
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

    def _markets_to_scan(self) -> list[str]:
        """현재 세션 → 스캔할 시장 목록.

        - 정규장(KRX_NXT, 09:00~15:20): KRX+NXT 동시 거래 → 양쪽 모두 스캔
        - NXT 단독(NXT_PRE, NXT_AFTER): NXT만 스캔
        - KRX 종가경매(KRX_CLOSE): KRX만 스캔
        - 그 외(CLOSED): 빈 리스트 — 호출자가 처리하지 않으면 빈 결과 반환
        """
        from scheduler.market_calendar import market_calendar
        session = market_calendar.get_market_session()
        if session == "KRX_NXT":
            return ["KRX", "NXT"]
        if session in ("NXT_PRE", "NXT_AFTER"):
            return ["NXT"]
        if session == "KRX_CLOSE":
            return ["KRX"]
        # CLOSED — 사이클이 도는 경우 안전하게 KRX 사용
        return ["KRX"]

    @staticmethod
    def _to_int(raw, default: int = 0) -> int:
        try:
            return int(float(str(raw).replace(",", "")))
        except (ValueError, TypeError):
            return default

    def _merge_market_results(
        self, results_per_market: list[tuple[str, list[dict]]]
    ) -> list[dict]:
        """여러 시장의 종목 리스트를 종목코드 기준 dedup.

        - 동일 종목코드가 양쪽 시장에 등장하면 거래대금(trade_amount)이 큰 쪽 채택
        - 각 종목에 `market` 필드(`"KRX"` / `"NXT"`) 부여
        """
        merged: dict[str, dict] = {}
        for market_label, stocks in results_per_market:
            for s in stocks:
                sym = s.get("symbol", s.get("code", ""))
                if not sym:
                    continue
                enriched = {**s, "market": market_label}
                existing = merged.get(sym)
                if existing is None:
                    merged[sym] = enriched
                    continue
                # 양쪽에 있으면 거래대금 큰 쪽 채택
                if self._to_int(enriched.get("trade_amount")) > self._to_int(
                    existing.get("trade_amount")
                ):
                    merged[sym] = enriched
        return list(merged.values())

    async def _get_volume_rank(self) -> list[dict]:
        markets = self._markets_to_scan()
        responses = await asyncio.gather(
            *[mcp_client.get_volume_rank(market=m) for m in markets],
            return_exceptions=True,
        )
        per_market: list[tuple[str, list[dict]]] = []
        for market_label, resp in zip(markets, responses):
            if isinstance(resp, Exception):
                logger.warning("거래량 순위 조회 실패 [{}]: {}", market_label, resp)
                continue
            if resp.success and resp.data:
                stocks = resp.data.get("stocks", resp.data.get("items", []))
                per_market.append((market_label, stocks))
        merged = self._merge_market_results(per_market)
        if len(markets) > 1:
            counts = {m: len(s) for m, s in per_market}
            logger.debug(
                "거래량 순위 시장별 합산: {} → dedup {}건", counts, len(merged)
            )
        return self._filter_untradeable(merged)

    async def _get_fluctuation_rank(self, sort: str) -> list[dict]:
        markets = self._markets_to_scan()
        responses = await asyncio.gather(
            *[mcp_client.get_fluctuation_rank(market=m, sort=sort) for m in markets],
            return_exceptions=True,
        )
        per_market: list[tuple[str, list[dict]]] = []
        for market_label, resp in zip(markets, responses):
            if isinstance(resp, Exception):
                logger.warning(
                    "등락률 순위({}) 조회 실패 [{}]: {}", sort, market_label, resp
                )
                continue
            if resp.success and resp.data:
                stocks = resp.data.get("stocks", resp.data.get("items", []))
                per_market.append((market_label, stocks))
        merged = self._merge_market_results(per_market)
        if len(markets) > 1:
            counts = {m: len(s) for m, s in per_market}
            logger.debug(
                "등락률 순위({}) 시장별 합산: {} → dedup {}건",
                sort, counts, len(merged),
            )
        return self._filter_untradeable(merged)

    @staticmethod
    def _format_trade_amount(raw) -> str:
        """거래대금(원) → '624억' / '8.5천억' 등 가독성 포맷"""
        try:
            v = float(str(raw).replace(",", ""))
        except (ValueError, TypeError):
            return ""
        if v <= 0:
            return ""
        if v >= 1_0000_0000_0000:  # 1조 이상
            return f"{v / 1_0000_0000_0000:.1f}조"
        if v >= 100_000_000:  # 1억 이상
            return f"{v / 100_000_000:,.0f}억"
        return f"{v / 10_000:,.0f}만"

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
            market = item.get("market", "")
            market_tag = f"[{market}] " if market else ""
            trade_amount = self._format_trade_amount(item.get("trade_amount"))
            amount_str = f" 거래대금:{trade_amount}" if trade_amount else ""
            # 전일 대비 거래량 증가율
            vol_inc = item.get("volume_increase_rate", "")
            try:
                vol_inc_f = float(str(vol_inc).replace(",", ""))
                vol_inc_str = f"(전일비{vol_inc_f:+.0f}%)" if vol_inc_f != 0 else ""
            except (ValueError, TypeError):
                vol_inc_str = ""
            lines.append(
                f"{i}. {market_tag}{name}({symbol}) {price}원 {change_rate}% "
                f"거래량:{volume}{vol_inc_str}{amount_str}"
            )
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
