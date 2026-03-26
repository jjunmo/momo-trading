"""AI Trading Agent 메인 루프 - 장중: 스캔→판단→분석→매매 / 장외: 성과 리뷰→피드백 학습"""
import asyncio
import json
from collections.abc import Callable

import pandas as pd
from loguru import logger

from agent.decision_maker import decision_maker
from agent.market_scanner import market_scanner
from analysis.chart_analyzer import ChartAnalysisResult, chart_analyzer
from analysis.feedback.context_builder import FeedbackContextBuilder
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.daily_plan import DAILY_PLAN_PROMPT, DAILY_PLAN_SYSTEM
from analysis.llm.prompts.final_review import FINAL_REVIEW_PROMPT, FINAL_REVIEW_SYSTEM
from analysis.llm.prompts.stock_analysis import STOCK_ANALYSIS_PROMPT, STOCK_ANALYSIS_SYSTEM
from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, EventType, event_bus
from realtime.event_detector import event_detector
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from strategy.aggressive_short import AggressiveShortStrategy
from strategy.risk_manager import risk_manager
from strategy.signal import TradeSignal
from strategy.stable_short import StableShortStrategy
from trading.enums import ActivityPhase, ActivityType, LLMTier, SignalAction, SignalUrgency
from trading.mcp_client import mcp_client


class TradingAgent:
    """
    AI 트레이딩 에이전트 — 장 시간에 맞춰 자동 운영

    장중: WebSocket 실시간 시세 → 이벤트 감지 → 즉시 분석/매매
    장외: 오늘 성과 리뷰 + 피드백 학습
    """

    def __init__(self):
        self.strategies = {
            "STABLE_SHORT": StableShortStrategy(),
            "AGGRESSIVE_SHORT": AggressiveShortStrategy(),
        }
        self._running = False
        self._active_trading_rules: dict = {}  # 활성 트레이딩 규칙 (프리마켓에서 로드)
        self._cycle_lock = asyncio.Lock()  # 사이클 동시 실행 방지
        self._last_cycle_time = None
        # 실시간 이벤트 중복 분석 방지 (종목별 쿨다운)
        self._analyzing: set[str] = set()
        self._cooldowns: dict[str, float] = {}  # symbol -> last_trigger_time
        self.EVENT_COOLDOWN_SEC = 120  # 동일 종목 재분석 최소 간격 (초)
        self.EXIT_COOLDOWN_SEC = 1800  # 손절/익절 후 재진입 차단 (30분)
        # 사이클 내 시장 컨텍스트 캐시 (Tier1/Tier2에 전달)
        self._market_context: str = ""
        # 시장 국면 (전략/리스크에 전달)
        self._market_regime: str = ""
        # 데이트레이딩 컨텍스트 캐시 (시간/손익/매매성적)
        self._trading_context: str = ""
        # 데이트레이딩 일일 기준 자산 (손익 계산용)
        self._daily_start_balance: float = 0.0
        # Claude Code 세션 ID (장중 → 장외 이어받기용)
        self._last_session_id: str | None = None
        # 종목코드 → 종목명 캐시 (WebSocket 이벤트에서 종목명 표시용)
        self._symbol_names: dict[str, str] = {}
        # 이중 매도 방지: 매도 진행 중인 종목 잠금
        self._selling: set[str] = set()
        self._sell_lock = asyncio.Lock()

    async def start(self) -> None:
        """에이전트 시작 - 실시간 이벤트 구독"""
        self._running = True
        event_bus.subscribe(EventType.VOLUME_SPIKE, self._on_market_event)
        event_bus.subscribe(EventType.PRICE_SURGE, self._on_market_event)
        event_bus.subscribe(EventType.PRICE_DROP, self._on_market_event)
        event_bus.subscribe(EventType.STOP_LOSS_HIT, self._on_stop_loss)
        event_bus.subscribe(EventType.TAKE_PROFIT_HIT, self._on_take_profit)
        logger.debug("AI Trading Agent 시작 — 실시간 이벤트 구독 활성화")

    async def stop(self) -> None:
        """에이전트 중지"""
        self._running = False
        self._analyzing.clear()
        self._selling.clear()
        logger.debug("AI Trading Agent 중지")

    async def _acquire_sell(self, symbol: str) -> bool:
        """매도 잠금 획득 — 이미 매도 중이면 False"""
        async with self._sell_lock:
            if symbol in self._selling:
                logger.debug("[{}] 이미 매도 진행 중 → 중복 매도 차단", symbol)
                return False
            self._selling.add(symbol)
            return True

    def _release_sell(self, symbol: str) -> None:
        """매도 잠금 해제"""
        self._selling.discard(symbol)

    def _resolve_name(self, symbol: str) -> str:
        """종목코드 → 종목명 반환 (캐시에 없으면 코드 그대로)"""
        return self._symbol_names.get(symbol, symbol)

    async def run_cycle(self) -> dict:
        """에이전트 1회 실행 사이클 — 장중이면 매매, 장외면 리뷰"""
        if self._cycle_lock.locked():
            logger.warning("사이클 이미 실행 중 — 중복 트리거 무시")
            return {"skipped": True, "reason": "cycle_already_running"}

        async with self._cycle_lock:
            if market_calendar.is_krx_trading_hours():
                # 데이트레이딩 모드: 매수 마감 시간 이후 신규 매수 차단
                # 스윙 모드: 오버나이트 보유 가능 → 장 마감(15:20)까지 매수 허용
                if settings.DAY_TRADING_ONLY:
                    from datetime import time as _time
                    from util.time_util import now_kst
                    cutoff = _time(settings.BUY_CUTOFF_HOUR, settings.BUY_CUTOFF_MINUTE)
                    if now_kst().time() >= cutoff:
                        logger.debug("매수 마감 시간({}) 경과 → 신규 매매 사이클 스킵", cutoff)
                        await activity_logger.log(
                            ActivityType.CYCLE, ActivityPhase.COMPLETE,
                            f"\u23f0 매수 마감({cutoff.strftime('%H:%M')}) — "
                            "신규 매수 차단, 보유종목 모니터링만 유지",
                        )
                        return {"skipped": True, "reason": "buy_cutoff"}
                return await self._run_trading_cycle()
            else:
                return await self._run_after_hours_cycle()

    async def _run_trading_cycle(self) -> dict:
        """장중 사이클: 스캔 → 분석 → 매매"""
        # Claude Code 세션 시작 (사이클 내 맥락 유지)
        from analysis.llm.claude_code_provider import ClaudeCodeProvider
        ClaudeCodeProvider.start_session()

        cycle_id = activity_logger.start_cycle()
        cycle_timer = activity_logger.timer()

        logger.info("=== Agent 장중 사이클 시작 ===")
        await event_bus.publish(Event(
            type=EventType.AGENT_CYCLE_START, source="trading_agent",
        ))
        await activity_logger.log(
            ActivityType.CYCLE, ActivityPhase.START,
            "\U0001f504 장중 매매 사이클 시작",
            cycle_id=cycle_id,
        )

        results = {"scanned": 0, "analyzed": 0, "signals": 0, "executed": 0, "selected_symbols": []}

        # AI 자율 한도 결정
        dynamic_limits = None
        if settings.AI_RISK_TUNING_ENABLED:
            try:
                from strategy.ai_risk_tuner import ai_risk_tuner
                dynamic_limits = await ai_risk_tuner.compute_limits(
                    risk_appetite=settings.RISK_APPETITE,
                    cycle_id=cycle_id,
                )
            except Exception as e:
                logger.warning("AI 한도 결정 실패, 기본값 사용: {}", str(e))

        try:
            # 0. 포트폴리오 스냅샷 (스캔 전 현금 확인, MCP 1회)
            from trading.account_manager import account_manager
            snapshot = {
                "cash": 0, "total_asset": 0,
                "holding_count": 0, "today_trade_count": 0,
            }
            try:
                balance, holdings = await account_manager.get_account_snapshot()
                if not balance.is_valid:
                    logger.error("계좌 조회 실패 → 매매 사이클 중단")
                    await activity_logger.log(
                        ActivityType.CYCLE, ActivityPhase.ERROR,
                        "🛑 계좌 조회 실패 → 매매 사이클 중단 (데이터 신뢰성 보호)",
                        cycle_id=cycle_id,
                    )
                    return results
                snapshot["cash"] = balance.cash
                snapshot["total_asset"] = balance.total_asset
                snapshot["holding_count"] = len(holdings)
                snapshot["holding_symbols"] = [h.symbol for h in holdings]
                snapshot["today_trade_count"] = await self._get_today_trade_count()
                snapshot["min_holding_price"] = min(
                    (h.current_price for h in holdings if h.current_price and h.current_price > 0),
                    default=0,
                )
                for h in holdings:
                    if h.symbol and h.name and h.name != h.symbol:
                        self._symbol_names[h.symbol] = h.name
            except Exception as e:
                logger.warning("포트폴리오 스냅샷 조회 실패, 기본값 사용: {}", str(e))

            if self._daily_start_balance == 0 and snapshot["total_asset"] > 0:
                self._daily_start_balance = snapshot["total_asset"]

            # 현금 부족 판정 → 매수만 차단, 스캔+매도 분석은 계속 진행
            eff_min_order_amount = (
                (dynamic_limits.get("min_buy_quantity", settings.MIN_BUY_QUANTITY)
                 if dynamic_limits else settings.MIN_BUY_QUANTITY)
                * 1000
            )
            min_price_ref = snapshot.get("min_holding_price", 0)
            buy_blocked = False
            if min_price_ref > 0 and snapshot["cash"] < min_price_ref:
                buy_blocked = True
                logger.info(
                    "현금 부족 → 매수 차단, 매도 분석 계속: {:,.0f}원 < 최소 보유주가 {:,.0f}원",
                    snapshot["cash"], min_price_ref,
                )
                await activity_logger.log(
                    ActivityType.CYCLE, ActivityPhase.IN_PROGRESS,
                    f"💰 현금 부족 → 매수 차단, 매도 분석 계속 ({snapshot['cash']:,.0f}원 < 최소 보유주가 {min_price_ref:,.0f}원)",
                    cycle_id=cycle_id,
                )

            # 1. 시장 스캔 + 종목 선별 (통합 1회 LLM 호출)
            scan_result = await market_scanner.scan(cycle_id=cycle_id, dynamic_limits=dynamic_limits)
            candidates = scan_result.get("selected", [])
            results["scanned"] = len(candidates)

            if not candidates:
                logger.debug("스캔 결과 선정 종목 없음, 사이클 종료")
                await activity_logger.log(
                    ActivityType.CYCLE, ActivityPhase.COMPLETE,
                    "\u2705 사이클 종료: 선정 종목 없음",
                    cycle_id=cycle_id,
                    execution_time_ms=activity_logger.elapsed_ms(cycle_timer),
                )
                return results

            # 종목명 캐시 갱신 (스캔 결과)
            for c in candidates:
                sym = c.get("symbol", "")
                nm = c.get("name", "")
                if sym and nm and nm != sym:
                    self._symbol_names[sym] = nm

            # 1b. 시장 국면 + 컨텍스트 빌드 (Tier1/Tier2/전략/리스크에 전달)
            self._market_regime = scan_result.get("market_regime", "")
            self._market_context = self._build_market_context(scan_result)

            # 1c. 데이트레이딩 컨텍스트 빌드 (시간/손익/매매성적)
            self._trading_context = await self._build_trading_context()

            # 선정 종목을 결과에 저장 (WebSocket 구독용)
            results["selected_symbols"] = [
                (c.get("symbol", ""), c.get("market", "KRX"))
                for c in candidates if c.get("symbol")
            ]

            # AI가 결정한 모니터링 임계값을 event_detector에 설정
            self._apply_scan_thresholds(candidates)

            # 2. 후보 종목별 심층 분석 + 전략 평가 + 매매 (병렬)
            # 세션 일시 중지 → 각 종목 분석은 독립 호출 (병렬 가능)
            # 스크리닝 맥락은 self._market_context로 프롬프트에 전달됨
            paused_sid = ClaudeCodeProvider.pause_session()

            semaphore = asyncio.Semaphore(3)
            executed_count = 0

            holding_syms = set(snapshot.get("holding_symbols", []))

            async def _analyze_with_limit(stock_info: dict) -> dict:
                nonlocal executed_count
                async with semaphore:
                    # 매도 방향 또는 보유종목은 매수가능 검사 면제
                    direction = stock_info.get("direction", "BUY")
                    symbol = stock_info.get("symbol", "")
                    is_holding = symbol in holding_syms
                    min_qty = (
                        (dynamic_limits or {}).get("min_buy_quantity", settings.MIN_BUY_QUANTITY)
                    )

                    if direction != "SELL" and not is_holding:
                        if buy_blocked:
                            return {"skipped": True, "reason": "현금 부족 (매수 차단)"}
                        from trading.kis_api import get_buying_power
                        bp = await get_buying_power(symbol)
                        if bp["success"] and bp["max_qty"] < min_qty:
                            logger.info(
                                "[{}] 매수가능수량 부족으로 스킵: {}주 < 최소 {}주",
                                symbol, bp["max_qty"], min_qty,
                            )
                            return {"skipped": True, "reason": f"매수가능수량 부족 ({bp['max_qty']}주)"}
                        stock_info["_buying_power"] = bp

                    r = await self._analyze_and_trade(
                        stock_info, cycle_id,
                        dynamic_limits=dynamic_limits,
                        portfolio_snapshot=snapshot,
                        executed_count_ref=lambda: executed_count,
                    )
                    if r.get("executed"):
                        executed_count += 1
                    return r

            all_results = await asyncio.gather(
                *[_analyze_with_limit(s) for s in candidates],
                return_exceptions=True,
            )

            # 병렬 분석 완료 → 세션 재개 (리포트/후속 처리용)
            if paused_sid:
                ClaudeCodeProvider.resume_session(paused_sid)

            for i, r in enumerate(all_results):
                if isinstance(r, Exception):
                    sym = candidates[i].get("symbol", "?")
                    logger.error("종목 분석 오류 ({}): {}", sym, str(r))
                    await activity_logger.log(
                        ActivityType.TIER1_ANALYSIS, ActivityPhase.ERROR,
                        f"\u274c [{sym}] 분석 오류: {str(r)[:100]}",
                        cycle_id=cycle_id,
                        symbol=sym,
                        error_message=str(r),
                    )
                elif isinstance(r, dict):
                    results["analyzed"] += 1
                    if r.get("signal"):
                        results["signals"] += 1
                    if r.get("executed"):
                        results["executed"] += 1

        except Exception as e:
            err_msg = str(e) or repr(e)
            logger.error("Agent 사이클 오류 ({}): {}", type(e).__name__, err_msg)
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.ERROR,
                f"\u274c 사이클 오류: [{type(e).__name__}] {err_msg[:100]}",
                cycle_id=cycle_id,
                error_message=err_msg,
            )

        from util.time_util import now_kst
        self._last_cycle_time = now_kst()
        elapsed = activity_logger.elapsed_ms(cycle_timer)

        await event_bus.publish(Event(
            type=EventType.AGENT_CYCLE_END, data=results, source="trading_agent",
        ))
        await activity_logger.log(
            ActivityType.CYCLE, ActivityPhase.COMPLETE,
            f"\u2705 사이클 완료: 분석 {results['analyzed']}건, "
            f"추천 {results['signals']}건, 소요 {elapsed / 1000:.1f}초",
            cycle_id=cycle_id,
            detail=results,
            execution_time_ms=elapsed,
        )
        # 세션 종료 (세션 ID 보존 — 장외 사이클에서 재개 가능)
        self._last_session_id = ClaudeCodeProvider.end_session()

        logger.info("=== Agent 장중 사이클 종료: {} ===", results)
        return results

    async def _analyze_and_trade(
        self, stock_info: dict, cycle_id: str,
        dynamic_limits: dict | None = None,
        portfolio_snapshot: dict | None = None,
        executed_count_ref: Callable | None = None,
    ) -> dict:
        """개별 종목 분석 → 전략 평가 → 매매 결정"""
        symbol = stock_info.get("symbol", "")
        name = stock_info.get("name", symbol)
        strategy_type = stock_info.get("strategy_type", "STABLE_SHORT")

        # 종목명 캐시 갱신
        if symbol and name and name != symbol:
            self._symbol_names[symbol] = name

        result = {"symbol": symbol, "signal": False, "executed": False}

        # 피드백 하드 룰: 연속 손실 차단 (매수만 차단, 매도/보유종목 분석은 허용)
        try:
            async with AsyncSessionLocal() as session:
                from analysis.feedback.performance_tracker import PerformanceTracker
                tracker = PerformanceTracker(session)
                consecutive = await tracker.get_consecutive_losses()
                if consecutive >= 5:
                    direction = stock_info.get("direction", "BUY")
                    snap_holdings = (portfolio_snapshot or {}).get("holding_symbols", [])
                    if direction != "SELL" and symbol not in snap_holdings:
                        logger.warning("[하드 룰] 연속 {}회 손실 → 매수 차단: {}", consecutive, symbol)
                        await activity_logger.log(
                            ActivityType.RISK_GATE, ActivityPhase.SKIP,
                            f"🛑 연속 {consecutive}회 손실 → 매수 차단 (하드 룰)",
                            cycle_id=cycle_id, symbol=symbol,
                        )
                        return result
                    logger.debug("[하드 룰] 연속 {}회 손실이지만 매도/보유종목 분석 허용: {}", consecutive, symbol)
        except Exception:
            pass

        # MCP로 데이터 병렬 조회 (일봉 60일 + 분봉 5분 + 현재가)
        price_resp, daily_resp, minute_resp = await asyncio.gather(
            mcp_client.get_current_price(symbol),
            mcp_client.get_daily_price(symbol, count=60),
            mcp_client.get_minute_price(symbol, period="5"),
        )

        current_price = 0
        if price_resp.success and price_resp.data:
            current_price = float(price_resp.data.get("price", price_resp.data.get("current_price", 0)))
        else:
            logger.warning("[{}] 현재가 조회 실패: {}", symbol, price_resp.error or "응답 없음")

        # 3b. DataFrame 변환 + 차트 종합 분석
        daily_df = pd.DataFrame()
        minute_df = None
        chart_result = ChartAnalysisResult()

        if daily_resp.success and daily_resp.data:
            daily_items = daily_resp.data.get("prices", daily_resp.data.get("items", []))
            if daily_items:
                daily_df = pd.DataFrame(daily_items)
                for col in ["open", "high", "low", "close"]:
                    if col in daily_df.columns:
                        daily_df[col] = pd.to_numeric(daily_df[col], errors="coerce")
                if "volume" in daily_df.columns:
                    daily_df["volume"] = pd.to_numeric(daily_df["volume"], errors="coerce")
            else:
                logger.warning("[{}] 일봉 응답은 성공이나 prices 비어있음", symbol)
        else:
            logger.warning("[{}] 일봉 조회 실패: {}", symbol, daily_resp.error or "응답 없음")

        if minute_resp.success and minute_resp.data:
            minute_items = minute_resp.data.get("prices", [])
            if minute_items:
                minute_df = pd.DataFrame(minute_items)
                for col in ["open", "high", "low", "close"]:
                    if col in minute_df.columns:
                        minute_df[col] = pd.to_numeric(minute_df[col], errors="coerce")
                if "volume" in minute_df.columns:
                    minute_df["volume"] = pd.to_numeric(minute_df["volume"], errors="coerce")

        if not daily_df.empty:
            chart_result = chart_analyzer.analyze(daily_df, minute_df)

        # 비보유종목 + 현금으로 1주 매수 불가 → Tier1 스킵 (LLM 비용 절감)
        holding_syms = (portfolio_snapshot or {}).get("holding_symbols", [])
        if symbol not in holding_syms and current_price > 0:
            available_cash = (portfolio_snapshot or {}).get("cash", 0)
            min_buy_cost = current_price * (
                dynamic_limits.get("min_buy_quantity", settings.MIN_BUY_QUANTITY)
                if dynamic_limits else settings.MIN_BUY_QUANTITY
            )
            if available_cash < min_buy_cost:
                logger.info(
                    "[{}] 현금 부족 → Tier1 스킵: {:,.0f}원 < {:,.0f}원/주",
                    symbol, available_cash, min_buy_cost,
                )
                await activity_logger.log(
                    ActivityType.TIER1_ANALYSIS, ActivityPhase.SKIP,
                    f"💰 [{name}] 현금 부족으로 Tier1 스킵 ({available_cash:,.0f}원 < {min_buy_cost:,.0f}원)",
                    cycle_id=cycle_id, symbol=symbol,
                )
                return result

        # 핵심 데이터 없으면 AI 분석 스킵 (LLM 비용 + 무의미한 HOLD 방지)
        if current_price == 0 and daily_df.empty:
            logger.warning("[{}] 현재가·일봉 모두 없음 → 분석 스킵", symbol)
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.SKIP,
                f"⚠️ [{name}] 데이터 부족으로 분석 스킵 (현재가·일봉 조회 실패)",
                cycle_id=cycle_id, symbol=symbol,
            )
            return result

        indicators = chart_result.indicators

        # 3c. 피드백 컨텍스트 빌드
        feedback_context = "매매 이력 없음"
        try:
            async with AsyncSessionLocal() as session:
                builder = FeedbackContextBuilder(session)
                rsi_val = indicators.get("rsi_14")
                feedback_context = await builder.build_full_context(
                    strategy_type=strategy_type,
                    symbol=symbol,
                    current_rsi=rsi_val,
                )
        except Exception as e:
            logger.warning("피드백 컨텍스트 빌드 실패: {}", str(e))

        # 3d. Tier 1 AI 심층 분석
        t1_timer = activity_logger.timer()
        await activity_logger.log(
            ActivityType.TIER1_ANALYSIS, ActivityPhase.START,
            f"\U0001f4ca [{name}] Tier1 분석 시작",
            cycle_id=cycle_id, symbol=symbol,
        )

        analysis = await self._tier1_analysis(
            symbol, name, current_price, chart_result,
            price_resp.data or {}, feedback_context,
            market_context=self._market_context,
            trading_context=self._trading_context,
            cycle_id=cycle_id,
        )
        t1_elapsed = activity_logger.elapsed_ms(t1_timer)

        if not analysis:
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                f"\U0001f4ca [{name}] Tier1: 분석 실패 (응답 파싱 불가)",
                cycle_id=cycle_id, symbol=symbol,
                llm_tier="TIER1",
                execution_time_ms=t1_elapsed,
            )
            return result

        recommendation = analysis.get("recommendation", "HOLD")

        # 스캔 파이프라인 SELL: 미보유 종목만 스킵, 보유 종목은 Tier2 리뷰 진행
        if recommendation == "SELL":
            is_holding = symbol in (portfolio_snapshot or {}).get("holding_symbols", [])
            if not is_holding:
                reason = analysis.get("reason") or "AI SELL 추천"
                await activity_logger.log(
                    ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                    f"\U0001f4ca [{name}] Tier1: SELL → 미보유 종목 매도 스킵 | {reason[:100]}",
                    cycle_id=cycle_id, symbol=symbol,
                    detail={
                        "recommendation": "SELL",
                        "reason": reason,
                        "confidence": analysis.get("confidence") or 0,
                    },
                    llm_provider=analysis.get("provider"),
                    llm_tier="TIER1",
                    execution_time_ms=t1_elapsed,
                    confidence=analysis.get("confidence") or 0,
                )
                return result
            # 보유종목 SELL → Tier2 리뷰 진행
            logger.info("[{}] 보유종목 SELL 추천 → Tier2 리뷰 진행", symbol)

        if recommendation == "HOLD":
            reason = analysis.get("reason") or analysis.get("summary", "판단 근거 없음")
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                f"\U0001f4ca [{name}] Tier1: HOLD → 스킵 | {reason[:100]}",
                cycle_id=cycle_id, symbol=symbol,
                detail={
                    "recommendation": "HOLD",
                    "reason": reason,
                    "confidence": analysis.get("confidence") or 0,
                    "key_factors": analysis.get("key_factors", []),
                },
                llm_provider=analysis.get("provider"),
                llm_tier="TIER1",
                execution_time_ms=t1_elapsed,
                confidence=analysis.get("confidence") or 0,
            )
            return result

        await activity_logger.log(
            ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
            f"\U0001f4ca [{name}] Tier1 완료: {analysis.get('recommendation', '')} "
            f"| 신뢰도 {(analysis.get('confidence') or 0):.0%}",
            cycle_id=cycle_id, symbol=symbol,
            detail={
                "recommendation": analysis.get("recommendation"),
                "reason": analysis.get("reason") or analysis.get("summary", ""),
                "target_price": analysis.get("target_price"),
                "stop_loss": analysis.get("stop_loss_price"),
            },
            llm_provider=analysis.get("provider"),
            llm_tier="TIER1",
            execution_time_ms=t1_elapsed,
            confidence=analysis.get("confidence"),
        )

        # ── [하드 게이트] 트레이딩 규칙 기반 검증 (Tier2 진행 전) ──
        tier1_confidence = analysis.get("confidence") or 0
        active_rules = self._active_trading_rules
        _param_overrides = active_rules.get("param_overrides", {})
        _validation_flags = active_rules.get("validation_flags", {})

        # (A) 신뢰도 게이트: 규칙이 지정한 최소 신뢰도 미달 시 차단
        rule_min_conf = None
        for scope in [strategy_type, "ALL"]:
            val = _param_overrides.get(scope, {}).get("min_confidence")
            if val is not None and (rule_min_conf is None or val > rule_min_conf):
                rule_min_conf = val

        is_sell_or_holding = (
            analysis.get("recommendation") == "SELL"
            or symbol in (portfolio_snapshot or {}).get("holding_symbols", [])
        )
        # 시장 국면별 신뢰도 임계값 동적 조정
        if rule_min_conf and not is_sell_or_holding:
            _regime_adj = {"BULL": -0.05, "THEME": -0.03, "SIDEWAYS": 0.0, "BEAR": 0.03}
            adj = _regime_adj.get(self._market_regime, 0.0)
            effective_min_conf = max(0.50, min(0.85, rule_min_conf + adj))

            if tier1_confidence < effective_min_conf:
                adj_note = f" (국면 {self._market_regime}: {rule_min_conf:.0%}→{effective_min_conf:.0%})" if adj != 0 else ""
                await activity_logger.log(
                    ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                    f"🚫 [{name}] 신뢰도 게이트 차단: {tier1_confidence:.0%} < "
                    f"실효 최소 {effective_min_conf:.0%}{adj_note}",
                    cycle_id=cycle_id, symbol=symbol,
                )
                return result

        # (B) RR 비율 코드 레벨 재검증 (LLM 보고값 vs 실제 계산)
        if _validation_flags.get("revalidate_rr_ratio"):
            t1_target = analysis.get("target_price") or 0
            t1_stop = analysis.get("stop_loss_price") or 0

            if current_price > 0 and t1_target > 0 and t1_stop > 0:
                code_reward = abs(t1_target - current_price)
                code_risk = abs(current_price - t1_stop)

                if code_risk > 0:
                    code_rr = code_reward / code_risk
                    rr_overrides = active_rules.get("rr_floor_overrides", {})
                    min_rr = rr_overrides.get(
                        self._market_regime,
                        risk_manager.RR_FLOOR.get(self._market_regime, 1.2),
                    )
                    if code_rr < min_rr:
                        await activity_logger.log(
                            ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                            f"🚫 [{name}] RR 비율 검증 실패: "
                            f"코드 계산 {code_rr:.2f}:1 < 최소 {min_rr}:1 "
                            f"(target={t1_target:,.0f}, stop={t1_stop:,.0f}, "
                            f"현재가={current_price:,.0f})",
                            cycle_id=cycle_id, symbol=symbol,
                        )
                        return result
                elif code_risk == 0 and analysis.get("recommendation") == "BUY":
                    await activity_logger.log(
                        ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                        f"🚫 [{name}] 손절가=현재가 → RR 계산 불가, 차단",
                        cycle_id=cycle_id, symbol=symbol,
                    )
                    return result

        # (C) 손절가 필수 검증 (매수 추천인데 손절가 없으면 차단)
        if _validation_flags.get("require_stop_loss_logging"):
            if analysis.get("recommendation") == "BUY":
                t1_stop = analysis.get("stop_loss_price") or 0
                if t1_stop <= 0:
                    await activity_logger.log(
                        ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                        f"🚫 [{name}] 손절가 미설정 차단 (require_stop_loss_logging 규칙)",
                        cycle_id=cycle_id, symbol=symbol,
                    )
                    return result

        # (D) 매수가능수량 부족 게이트: BUY 추천인데 매수 불가 → Tier2 스킵
        if analysis.get("recommendation") == "BUY":
            min_buy_qty = (
                dynamic_limits.get("min_buy_quantity", settings.MIN_BUY_QUANTITY)
                if dynamic_limits else settings.MIN_BUY_QUANTITY
            )
            bp = stock_info.get("_buying_power")
            if bp and bp.get("success") and bp["max_qty"] < min_buy_qty:
                await activity_logger.log(
                    ActivityType.RISK_GATE, ActivityPhase.SKIP,
                    f"💰 [{name}] 매수가능수량 부족 → Tier2 스킵 "
                    f"(가능 {bp['max_qty']}주 < 최소 {min_buy_qty}주)",
                    cycle_id=cycle_id, symbol=symbol,
                )
                return result

        # 3d. Tier 2 최종 검토 (모든 BUY에 대해 필수 실행)
        t2_timer = activity_logger.timer()
        await activity_logger.log(
            ActivityType.TIER2_REVIEW, ActivityPhase.START,
            f"\U0001f9e0 [{name}] Tier2 최종 검토 시작",
            cycle_id=cycle_id, symbol=symbol,
        )

        final = await self._tier2_review(
            symbol, name, current_price, strategy_type, analysis,
            feedback_context=feedback_context,
            chart_result=chart_result,
            dynamic_limits=dynamic_limits,
            market_context=self._market_context,
            trading_context=self._trading_context,
            portfolio_snapshot=portfolio_snapshot,
            cycle_id=cycle_id,
        )
        t2_elapsed = activity_logger.elapsed_ms(t2_timer)

        if not final or not final.get("approved"):
            reason = final.get("reason", "") if final else "응답 없음"
            await activity_logger.log(
                ActivityType.TIER2_REVIEW, ActivityPhase.COMPLETE,
                f"\U0001f9e0 [{name}] Tier2: 미승인 - {reason[:80]}",
                cycle_id=cycle_id, symbol=symbol,
                llm_provider=final.get("provider") if final else None,
                llm_tier="TIER2",
                execution_time_ms=t2_elapsed,
            )
            logger.debug("Tier 2 검토 미승인: {} - {}", symbol, reason)
            return result

        await activity_logger.log(
            ActivityType.TIER2_REVIEW, ActivityPhase.COMPLETE,
            f"\U0001f9e0 [{name}] Tier2: \u2705 승인"
            + (f" | 수량 {final.get('suggested_quantity')}주" if final.get("suggested_quantity") else ""),
            cycle_id=cycle_id, symbol=symbol,
            detail={
                "approved": True,
                "reason": final.get("reason", ""),
                "suggested_quantity": final.get("suggested_quantity"),
                "entry_price": final.get("entry_price"),
                "target_price": final.get("target_price"),
            },
            llm_provider=final.get("provider"),
            llm_tier="TIER2",
            execution_time_ms=t2_elapsed,
        )

        # 4. 전략 적용 — Tier2 승인 시 AI 결정을 우선, 전략은 보조
        strategy = self.strategies.get(strategy_type)

        # Tier2가 수량/가격까지 제시한 경우 → AI 결정으로 직접 시그널 생성
        if final.get("suggested_quantity") and final.get("entry_price"):
            t2_action = final.get("action", analysis.get("recommendation", "BUY"))
            action = SignalAction.BUY if t2_action.upper() in ("BUY", "CAUTIOUS BUY") else SignalAction.SELL

            stop_loss_price = final.get("stop_loss_price")
            if not stop_loss_price and strategy:
                sl_pct = getattr(strategy, "stop_loss_pct", None) or -3
                stop_loss_price = final["entry_price"] * (1 + sl_pct / 100)

            target_price = final.get("target_price")
            if not target_price and strategy:
                tp_pct = getattr(strategy, "take_profit_pct", None) or 5
                target_price = final["entry_price"] * (1 + tp_pct / 100)

            signal = TradeSignal(
                symbol=symbol,
                stock_id=stock_info.get("stock_id", ""),
                action=action,
                strength=analysis.get("confidence", 0.7),
                suggested_price=final["entry_price"],
                suggested_quantity=final["suggested_quantity"],
                target_price=target_price,
                stop_loss_price=stop_loss_price,
                urgency=SignalUrgency.IMMEDIATE,
                strategy_type=strategy_type,
                reason=final.get("reason", "Tier2 승인"),
                confidence=analysis.get("confidence", 0.7),
            )

            result["signal"] = True
            await activity_logger.log(
                ActivityType.STRATEGY_EVAL, ActivityPhase.COMPLETE,
                f"\U0001f4c8 [{name}] Tier2 승인 기반 시그널: {action.value} "
                f"{signal.suggested_quantity}주 @{signal.suggested_price:,.0f}원",
                cycle_id=cycle_id, symbol=symbol,
            )
        else:
            # Tier2가 구체적 수량/가격을 제시하지 않은 경우 → 전략 평가로 폴백
            analysis_for_strategy = {
                **analysis,
                "indicators": indicators,
                "chart_result": chart_result,
                "symbol": symbol,
                "stock_id": stock_info.get("stock_id", ""),
                "current_price": current_price,
            }

            if not strategy:
                return result

            signal = await strategy.evaluate(analysis_for_strategy, market_regime=self._market_regime)
            if not signal or signal.action == SignalAction.HOLD:
                await activity_logger.log(
                    ActivityType.STRATEGY_EVAL, ActivityPhase.COMPLETE,
                    f"\U0001f4c8 [{name}] 전략 평가: HOLD → 스킵",
                    cycle_id=cycle_id, symbol=symbol,
                )
                return result

            result["signal"] = True
            await activity_logger.log(
                ActivityType.STRATEGY_EVAL, ActivityPhase.COMPLETE,
                f"\U0001f4c8 [{name}] 전략({strategy_type}): {signal.action.value} "
                f"{signal.suggested_quantity or 0}주 @{(signal.suggested_price or 0):,.0f}원",
                cycle_id=cycle_id, symbol=symbol,
            )

            # Tier 2에서 제안한 값이 있으면 적용
            if final.get("suggested_quantity"):
                signal.suggested_quantity = final["suggested_quantity"]
            if final.get("entry_price"):
                signal.suggested_price = final["entry_price"]
            if final.get("target_price"):
                signal.target_price = final["target_price"]
            if final.get("stop_loss_price"):
                signal.stop_loss_price = final["stop_loss_price"]

        # AI가 결정한 손절/익절/트레일링 스탑을 event_detector에 설정
        self._apply_trade_thresholds(symbol, analysis, final)

        # 4.5 매도 시 보유 여부 확인 — 미보유 종목 매도 차단
        if signal.action == SignalAction.SELL:
            snap = portfolio_snapshot or {}
            holding_symbols = snap.get("holding_symbols", [])
            if symbol not in holding_symbols:
                logger.debug("미보유 종목 매도 스킵: {} (보유: {})", symbol, holding_symbols)
                await activity_logger.log(
                    ActivityType.RISK_CHECK, ActivityPhase.SKIP,
                    f"🚫 [{name}] 미보유 종목 매도 차단",
                    cycle_id=cycle_id, symbol=symbol,
                )
                return result

        # 5. 리스크 검사
        snap = portfolio_snapshot or {}
        risk_result = await risk_manager.check(
            signal=signal,
            portfolio_cash=snap.get("cash", 0),
            portfolio_budget=snap.get("total_asset", 0),
            today_trade_count=snap.get("today_trade_count", 0),
            current_holding_count=snap.get("holding_count", 0),
            cycle_id=cycle_id,
            dynamic_limits=dynamic_limits,
            market_regime=self._market_regime,
        )

        if not risk_result.get("approved"):
            logger.debug("리스크 검사 미통과: {} - {}", symbol, risk_result.get("reason"))
            return result

        if risk_result.get("adjusted_quantity"):
            signal.suggested_quantity = risk_result["adjusted_quantity"]

        # 6. 매수 시 주문 직전 매수가능수량 재조회 (병렬 주문으로 가용금액 변동 반영)
        if signal.action == SignalAction.BUY:
            min_qty = (
                (dynamic_limits or {}).get("min_buy_quantity", settings.MIN_BUY_QUANTITY)
            )
            from trading.kis_api import get_buying_power
            bp = await get_buying_power(symbol)
            if bp["success"]:
                max_qty = bp["max_qty"]
                if max_qty < min_qty:
                    logger.info(
                        "[{}] 매수가능수량 부족으로 주문 포기: {}주 < 최소 {}주",
                        symbol, max_qty, min_qty,
                    )
                    await activity_logger.log(
                        ActivityType.RISK_CHECK, ActivityPhase.SKIP,
                        f"💰 [{name}] 매수가능수량 부족 → 주문 포기 "
                        f"(가능 {max_qty}주 < 최소 {min_qty}주)",
                        cycle_id=cycle_id, symbol=symbol,
                    )
                    return result
                if max_qty < signal.suggested_quantity:
                    logger.info(
                        "[{}] 매수가능수량으로 수량 조정: {}주 → {}주",
                        symbol, signal.suggested_quantity, max_qty,
                    )
                    signal.suggested_quantity = max_qty
            # bp 실패 시 → 기존 수량 유지, KIS가 최종 판단

            # 매수 시 시장가 주문 (미체결 방지)
            signal.suggested_price = None

        # 7. 매매 결정 (자율/반자율) — AI 분석 컨텍스트를 TradeResult에 전달
        analysis_context = {
            "ai_recommendation": analysis.get("recommendation"),
            "ai_confidence": analysis.get("confidence"),
            "ai_target_price": analysis.get("target_price"),
            "ai_stop_loss_price": analysis.get("stop_loss_price"),
            "entry_rsi": indicators.get("rsi_14"),
            "entry_macd_hist": indicators.get("macd_histogram"),
            "market_regime": self._market_regime,
            "strategy_type": strategy_type,
            "stock_name": name,
        }

        exec_result = await decision_maker.execute(
            signal, cycle_id=cycle_id, analysis_context=analysis_context,
        )
        result["executed"] = exec_result.get("success", False)

        return result

    async def _run_after_hours_cycle(self) -> dict:
        """장외 사이클: 오늘 데이트레이딩 성과 리뷰 (피드백 학습용)"""
        from analysis.llm.claude_code_provider import ClaudeCodeProvider
        from trading.account_manager import account_manager
        from util.time_util import now_kst

        ClaudeCodeProvider.start_session()

        cycle_id = activity_logger.start_cycle()
        cycle_timer = activity_logger.timer()

        logger.info("=== Agent 장 마감 리뷰 시작 ===")
        await event_bus.publish(Event(
            type=EventType.AGENT_CYCLE_START, source="trading_agent",
        ))
        await activity_logger.log(
            ActivityType.CYCLE, ActivityPhase.START,
            "\U0001f319 장 마감 리뷰 시작 — 오늘 매매 성과 분석",
            cycle_id=cycle_id,
        )

        results = {"mode": "AFTER_HOURS", "review_generated": False}

        try:
            # 1. 오늘 시장 마감 데이터 수집 (MCP)
            market_close_data, volume_rank_data, surge_data, drop_data = await self._collect_market_close_data()

            # 2. 포트폴리오 현황 (데이트레이딩이면 청산 완료 상태)
            balance = await account_manager.get_balance()

            cash_ratio = 0.0
            if balance.total_asset > 0:
                cash_ratio = (balance.cash / balance.total_asset) * 100

            # 3. 오늘 활동 집계
            today_date = now_kst().date()
            activity_summary = "활동 없음"
            today_cycles = 0
            today_analyses = 0
            today_recommendations = 0
            today_orders = 0

            try:
                async with AsyncSessionLocal() as session:
                    from repositories.agent_activity_repository import AgentActivityRepository
                    activity_repo = AgentActivityRepository(session)
                    activity_counts = await activity_repo.count_by_date(today_date)
                    activities = await activity_repo.get_by_date(today_date, limit=50)

                    today_cycles = activity_counts.get("CYCLE", 0) // 2
                    today_analyses = activity_counts.get("TIER1_ANALYSIS", 0)
                    today_recommendations = activity_counts.get("DECISION", 0)
                    today_orders = activity_counts.get("ORDER", 0)

                    if activities:
                        summary_lines = []
                        for a in activities[-20:]:
                            summary_lines.append(f"[{a.activity_type}/{a.phase}] {a.summary}")
                        activity_summary = "\n".join(summary_lines)
            except Exception as e:
                logger.warning("활동 집계 실패: {}", str(e))

            # 4. 과거 매매 성과
            performance_summary = "매매 이력 없음"
            try:
                from analysis.feedback.performance_tracker import PerformanceTracker
                async with AsyncSessionLocal() as session:
                    tracker = PerformanceTracker(session)
                    stats = await tracker.get_overall_stats()
                    overall = stats.get("overall")
                    if overall and overall.total_trades > 0:
                        performance_summary = (
                            f"총 {overall.total_trades}거래, "
                            f"승률 {overall.win_rate * 100:.1f}%, "
                            f"총손익 {overall.total_pnl:+,.0f}원"
                        )
            except Exception as e:
                logger.warning("성과 요약 실패: {}", str(e))

            # 5. 오버나이트 보유종목 현황 (스윙 모드)
            overnight_holdings_text = "없음 (당일 청산 모드)" if settings.DAY_TRADING_ONLY else "없음"
            if not settings.DAY_TRADING_ONLY:
                try:
                    async with AsyncSessionLocal() as session:
                        from repositories.trade_result_repository import TradeResultRepository
                        from strategy.holding_policy import _calc_hold_days, _get_max_hold_days
                        repo = TradeResultRepository(session)
                        open_positions = await repo.get_all_open()
                        if open_positions:
                            # 실제 KIS 보유종목과 교차 검증
                            actual_symbols = set()
                            try:
                                from trading.account_manager import account_manager
                                actual_holdings = await account_manager.get_holdings()
                                actual_symbols = {h.symbol for h in actual_holdings if h.quantity > 0}
                            except Exception:
                                # 조회 실패 시 DB 그대로 사용 (정리 불가)
                                actual_symbols = {tr.stock_symbol for tr in open_positions}

                            orphan_count = 0
                            lines = []
                            for tr in open_positions:
                                if tr.stock_symbol not in actual_symbols:
                                    # 고아 레코드 → exit_at + 손익 계산
                                    from util.time_util import now_kst
                                    now = now_kst()
                                    tr.exit_at = now
                                    tr.exit_reason = "ORPHAN_CLEANUP"

                                    # exit_price 추정: 현재가 조회
                                    exit_price = 0.0
                                    try:
                                        resp = await mcp_client.get_current_price(tr.stock_symbol)
                                        if resp.success and resp.data:
                                            exit_price = float(resp.data.get("price", 0))
                                    except Exception:
                                        pass

                                    if exit_price > 0 and tr.entry_price > 0:
                                        tr.exit_price = exit_price
                                        tr.pnl = (exit_price - tr.entry_price) * tr.quantity
                                        tr.return_pct = round(
                                            (exit_price - tr.entry_price) / tr.entry_price * 100, 2
                                        )
                                        tr.is_win = tr.pnl > 0
                                        tr.hold_days = (now - tr.entry_at).days if tr.entry_at else 0

                                    orphan_count += 1
                                    continue

                                hold_days = _calc_hold_days(tr)
                                max_days = _get_max_hold_days(tr.strategy_type, settings)
                                conf = tr.ai_confidence or 0.0
                                target_pct = ""
                                if tr.ai_target_price and tr.entry_price > 0:
                                    target_pct = f", 목표 도달률 {(tr.entry_price / tr.ai_target_price) * 100:.0f}%"
                                lines.append(
                                    f"- {tr.stock_name}({tr.stock_symbol}): "
                                    f"보유 {hold_days}/{max_days}일, "
                                    f"신뢰도 {conf:.2f}, "
                                    f"전략 {tr.strategy_type}"
                                    f"{target_pct}"
                                )

                            if orphan_count:
                                await session.commit()
                                logger.warning("고아 TradeResult {}건 정리 완료", orphan_count)

                            overnight_holdings_text = "\n".join(lines) if lines else "없음"
                except Exception as e:
                    logger.warning("오버나이트 보유종목 조회 실패: {}", str(e))

            # 6. LLM으로 성과 리뷰
            t1_timer = activity_logger.timer()
            await activity_logger.log(
                ActivityType.DAILY_PLAN, ActivityPhase.START,
                "\U0001f4cb 장 마감 성과 리뷰 생성 중...",
                cycle_id=cycle_id,
            )

            if settings.DAY_TRADING_ONLY:
                trading_mode_text = "당일 청산 모드 (데이트레이딩): 당일 매수 → 당일 청산 필수, 오버나이트 보유 없음"
            else:
                trading_mode_text = (
                    "스윙 모드: 장 상황에 따라 AI가 당일 청산 또는 오버나이트 보유를 판단. "
                    "유망 종목(수익 중 + 고신뢰도 + 목표 미도달)은 오버나이트 보유 가능. "
                    "오버나이트 보유종목이 있다면 overnight_evaluation에 내일 전망을 반드시 작성."
                )

            prompt = DAILY_PLAN_PROMPT.format(
                today_date=today_date,
                trading_mode=trading_mode_text,
                market_close_data=market_close_data,
                volume_rank_data=volume_rank_data,
                surge_data=surge_data,
                drop_data=drop_data,
                total_asset=balance.total_asset,
                cash=balance.cash,
                cash_ratio=cash_ratio,
                stock_value=balance.stock_value,
                total_pnl=balance.total_pnl,
                total_pnl_rate=balance.total_pnl_rate,
                today_cycles=today_cycles,
                today_analyses=today_analyses,
                today_recommendations=today_recommendations,
                today_orders=today_orders,
                activity_summary=activity_summary,
                performance_summary=performance_summary,
                overnight_holdings_text=overnight_holdings_text,
            )

            result_text, provider = await llm_factory.generate_tier1(
                prompt, system_prompt=DAILY_PLAN_SYSTEM
            )
            t1_elapsed = activity_logger.elapsed_ms(t1_timer)

            parsed = self._parse_json(result_text)
            if parsed:
                results["review_generated"] = True

                today_review = parsed.get("today_review", "")
                trade_eval = parsed.get("trade_evaluation", {})
                success_patterns = parsed.get("success_patterns", [])
                failure_patterns = parsed.get("failure_patterns", [])
                feedback = parsed.get("feedback_for_tomorrow", {})
                risk_alerts = parsed.get("risk_alerts", [])

                summary_msg = "\U0001f4cb 장 마감 리뷰 완료"
                if today_review:
                    summary_msg += f"\n\U0001f4dd 리뷰: {today_review[:150]}"
                if trade_eval.get("total_trades"):
                    summary_msg += (
                        f"\n\U0001f4ca 매매: {trade_eval['total_trades']}건 "
                        f"(수익 {trade_eval.get('profitable_trades', 0)}건, "
                        f"손실 {trade_eval.get('loss_trades', 0)}건)"
                    )
                if success_patterns:
                    summary_msg += f"\n\u2705 성공 패턴: {success_patterns[0][:80]}"
                if failure_patterns:
                    summary_msg += f"\n\u274c 실패 패턴: {failure_patterns[0][:80]}"
                if feedback.get("system_improvement"):
                    summary_msg += f"\n\U0001f527 개선: {feedback['system_improvement'][:80]}"
                if risk_alerts:
                    summary_msg += f"\n\u26a0\ufe0f 리스크: {', '.join(risk_alerts[:3])}"

                await activity_logger.log(
                    ActivityType.DAILY_PLAN, ActivityPhase.COMPLETE,
                    summary_msg,
                    cycle_id=cycle_id,
                    detail=parsed,
                    llm_provider=provider,
                    llm_tier="TIER1",
                    execution_time_ms=t1_elapsed,
                )

                # 일일 리포트 DB 저장
                try:
                    await self._save_daily_report(
                        today_date, parsed,
                        today_cycles=today_cycles,
                        today_analyses=today_analyses,
                        today_recommendations=today_recommendations,
                        today_orders=today_orders,
                    )
                except Exception as e:
                    logger.warning("일일 리포트 저장 실패: {}", str(e))

                # 일일 리뷰 → 트레이딩 규칙 자동 생성 (내일 코드 레벨 강제 적용)
                try:
                    from analysis.feedback.trading_rules import trading_rule_engine
                    rules = await trading_rule_engine.generate_rules_from_review(
                        parsed, today_date,
                    )
                    if rules:
                        rule_summary = ", ".join(
                            f"{r.param_name}={r.param_value}" for r in rules
                        )
                        await activity_logger.log(
                            ActivityType.TRADING_RULE, ActivityPhase.COMPLETE,
                            f"📋 트레이딩 규칙 {len(rules)}건 생성 (내일 자동 적용): {rule_summary}",
                            cycle_id=cycle_id,
                            detail=[{"param": r.param_name, "value": r.param_value, "reason": r.reason} for r in rules],
                        )
                except Exception as e:
                    logger.warning("트레이딩 규칙 생성 실패: {}", str(e))
            else:
                await activity_logger.log(
                    ActivityType.DAILY_PLAN, ActivityPhase.ERROR,
                    "\u274c 장 마감 리뷰 생성 실패 (응답 파싱 불가)",
                    cycle_id=cycle_id,
                    llm_provider=provider,
                    execution_time_ms=t1_elapsed,
                )

        except Exception as e:
            logger.error("장외 사이클 오류: {}", str(e))
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.ERROR,
                f"\u274c 장외 사이클 오류: {str(e)[:100]}",
                cycle_id=cycle_id,
                error_message=str(e),
            )

        from util.time_util import now_kst
        self._last_cycle_time = now_kst()
        elapsed = activity_logger.elapsed_ms(cycle_timer)

        next_open = market_calendar.next_krx_open()
        await event_bus.publish(Event(
            type=EventType.AGENT_CYCLE_END, data=results, source="trading_agent",
        ))
        await activity_logger.log(
            ActivityType.CYCLE, ActivityPhase.COMPLETE,
            f"\U0001f319 장 마감 리뷰 완료 (소요 {elapsed / 1000:.1f}초) "
            f"| 다음 장 시작: {next_open.strftime('%m/%d %H:%M')}",
            cycle_id=cycle_id,
            detail=results,
            execution_time_ms=elapsed,
        )
        ClaudeCodeProvider.end_session()
        self._last_session_id = None

        logger.info("=== Agent 장 마감 리뷰 종료 ===")
        return results

    async def _save_daily_report(
        self, report_date, parsed: dict,
        today_cycles: int = 0, today_analyses: int = 0,
        today_recommendations: int = 0, today_orders: int = 0,
    ) -> None:
        """장 마감 리뷰 AI 결과를 DailyReport에 저장 (데이트레이딩 성과 리뷰)"""
        from models.daily_report import DailyReport
        from repositories.daily_report_repository import DailyReportRepository

        feedback = parsed.get("feedback_for_tomorrow", {})
        trade_eval = parsed.get("trade_evaluation", {})

        # 피드백/패턴을 strategy_stats에 저장 (피드백 시스템이 참조)
        stats = {
            "risk_alerts": parsed.get("risk_alerts", []),
            "success_patterns": parsed.get("success_patterns", []),
            "failure_patterns": parsed.get("failure_patterns", []),
            "feedback": feedback,
            "trade_evaluation": trade_eval,
        }

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = DailyReportRepository(session)
                report = await repo.get_by_date(report_date)

                report_data = {
                    "total_cycles": today_cycles,
                    "total_analyses": today_analyses,
                    "total_recommendations": today_recommendations,
                    "total_orders": today_orders,
                    "market_summary": parsed.get("today_review", ""),
                    "performance_review": json.dumps(trade_eval, ensure_ascii=False),
                    "lessons_learned": feedback.get("system_improvement", ""),
                    "next_day_plan": "",  # 데이트레이딩: 익일 전략 불필요
                    "top_picks": "[]",  # 데이트레이딩: 관심종목 불필요
                    "strategy_stats": json.dumps(stats, ensure_ascii=False),
                }

                if report:
                    for k, v in report_data.items():
                        setattr(report, k, v)
                    logger.debug("일일 리포트 갱신 완료: {}", report_date)
                else:
                    report = DailyReport(report_date=report_date, **report_data)
                    session.add(report)
                    logger.debug("일일 리포트 생성 완료: {}", report_date)

    async def _collect_market_close_data(self) -> tuple[str, str, str, str]:
        """오늘 시장 마감 데이터 수집 (MCP) — 장외 리뷰용

        Returns:
            (market_close_data, volume_rank_data, surge_data, drop_data)
        """
        market_close_data = "시장 데이터 조회 실패"
        volume_rank_text = "데이터 없음"
        surge_text = "데이터 없음"
        drop_text = "데이터 없음"

        try:
            # 병렬로 시장 데이터 수집
            volume_resp, surge_resp, drop_resp = await asyncio.gather(
                mcp_client.get_volume_rank(),
                mcp_client.get_fluctuation_rank(sort="top"),
                mcp_client.get_fluctuation_rank(sort="bottom"),
                return_exceptions=True,
            )

            # 거래량 상위
            if not isinstance(volume_resp, Exception) and volume_resp.success and volume_resp.data:
                items = volume_resp.data.get("stocks", volume_resp.data.get("items", []))
                if items:
                    lines = []
                    for i, item in enumerate(items[:15], 1):
                        name = item.get("name", "")
                        symbol = item.get("symbol", item.get("code", ""))
                        price = item.get("price", item.get("current_price", ""))
                        change_rate = item.get("change_rate", "")
                        volume = item.get("volume", "")
                        lines.append(f"{i}. {name}({symbol}) {price}원 {change_rate}% 거래량:{volume}")
                    volume_rank_text = "\n".join(lines)

            # 등락률 상위 (급등)
            if not isinstance(surge_resp, Exception) and surge_resp.success and surge_resp.data:
                items = surge_resp.data.get("stocks", surge_resp.data.get("items", []))
                if items:
                    lines = []
                    for i, item in enumerate(items[:15], 1):
                        name = item.get("name", "")
                        symbol = item.get("symbol", item.get("code", ""))
                        price = item.get("price", item.get("current_price", ""))
                        change_rate = item.get("change_rate", "")
                        lines.append(f"{i}. {name}({symbol}) {price}원 {change_rate}%")
                    surge_text = "\n".join(lines)

            # 등락률 하위 (급락)
            if not isinstance(drop_resp, Exception) and drop_resp.success and drop_resp.data:
                items = drop_resp.data.get("stocks", drop_resp.data.get("items", []))
                if items:
                    lines = []
                    for i, item in enumerate(items[:15], 1):
                        name = item.get("name", "")
                        symbol = item.get("symbol", item.get("code", ""))
                        price = item.get("price", item.get("current_price", ""))
                        change_rate = item.get("change_rate", "")
                        lines.append(f"{i}. {name}({symbol}) {price}원 {change_rate}%")
                    drop_text = "\n".join(lines)

            # 시장 요약은 등락률 상위/하위 데이터로 판단
            market_close_data = "거래량/등락률 상위 데이터로 오늘 시장 흐름 파악"

        except Exception as e:
            logger.warning("시장 마감 데이터 수집 실패: {}", str(e))

        return market_close_data, volume_rank_text, surge_text, drop_text

    async def _get_stock_trend_summary(self, symbol: str, name: str) -> str:
        """종목 일봉 기반 간단 추세 요약 (장 마감 후 사용)"""
        try:
            resp = await mcp_client.get_daily_price(symbol, count=20)
            if not resp.success or not resp.data:
                return ""

            prices = resp.data.get("prices", [])
            if len(prices) < 5:
                return ""

            # 최근 5일 종가 추출
            recent = prices[:5]
            closes = [float(p.get("close", 0)) for p in recent if float(p.get("close", 0)) > 0]
            if len(closes) < 3:
                return ""

            latest = closes[0]
            avg_5 = sum(closes) / len(closes)

            # 20일 평균
            all_closes = [float(p.get("close", 0)) for p in prices[:20] if float(p.get("close", 0)) > 0]
            avg_20 = sum(all_closes) / len(all_closes) if all_closes else latest

            # 5일 등락률
            change_5d = ((closes[0] - closes[-1]) / closes[-1] * 100) if closes[-1] > 0 else 0

            # 추세 판단
            if latest > avg_5 > avg_20:
                trend = "상승추세"
            elif latest < avg_5 < avg_20:
                trend = "하락추세"
            else:
                trend = "횡보"

            # 최근 거래량 추이
            volumes = [int(p.get("volume", 0)) for p in recent if int(p.get("volume", 0)) > 0]
            vol_text = ""
            if len(volumes) >= 3:
                avg_vol = sum(volumes) / len(volumes)
                if volumes[0] > avg_vol * 1.5:
                    vol_text = ", 거래량 급증"
                elif volumes[0] < avg_vol * 0.5:
                    vol_text = ", 거래량 감소"

            return (
                f"- {name}({symbol}): {trend} | "
                f"종가 {latest:,.0f}원 | 5일 {change_5d:+.1f}% | "
                f"5MA {avg_5:,.0f} / 20MA {avg_20:,.0f}{vol_text}"
            )
        except Exception as e:
            logger.debug("종목 추세 요약 실패 ({}): {}", symbol, str(e))
            return ""

    def _build_market_context(self, scan_result: dict) -> str:
        """시장 스캔 결과에서 Tier1/Tier2용 시장 컨텍스트 빌드"""
        parts = []

        # market_regime (개선된 프롬프트에서 제공)
        regime = scan_result.get("market_regime", "")
        if regime:
            parts.append(f"시장 국면: {regime}")

        # market_analysis (개선된 프롬프트에서 제공)
        analysis = scan_result.get("market_analysis", scan_result.get("market_summary", ""))
        if analysis:
            parts.append(f"시장 분석: {analysis}")

        # leading_sectors
        sectors = scan_result.get("leading_sectors", [])
        if sectors:
            parts.append(f"주도 섹터: {', '.join(sectors)}")

        if not parts:
            return "시장 컨텍스트 없음"

        return "\n".join(parts)

    async def _build_trading_context(self) -> str:
        """매매 컨텍스트 (프롬프트 주입용)"""
        from util.time_util import now_kst
        from trading.account_manager import account_manager

        now = now_kst()

        # 강제 청산까지 남은 분
        close_time = now.replace(
            hour=settings.FORCE_LIQUIDATION_HOUR,
            minute=settings.FORCE_LIQUIDATION_MINUTE,
            second=0, microsecond=0,
        )
        minutes_left = max(0, int((close_time - now).total_seconds() / 60))

        # 일일 손익
        daily_pnl_pct = 0.0
        if self._daily_start_balance > 0:
            try:
                balance = await account_manager.get_balance()
                daily_pnl_pct = (
                    (balance.total_asset - self._daily_start_balance)
                    / self._daily_start_balance * 100
                )
            except Exception:
                pass

        # 오늘 매매 성적
        stats = await self._get_today_trade_stats()

        if settings.DAY_TRADING_ONLY:
            time_info = f"강제 청산까지: {minutes_left}분"
        else:
            time_info = f"장 마감까지: {minutes_left}분 (스윙: 오버나이트 보유 가능)"

        context = (
            f"현재 시각: {now.strftime('%H:%M')} | "
            f"{time_info}\n"
            f"오늘 누적 손익: {daily_pnl_pct:+.2f}% | "
            f"매매 성적: {stats['wins']}승 {stats['losses']}패 "
            f"(총 {stats['total']}건)"
        )

        if not settings.DAY_TRADING_ONLY:
            context += (
                f"\n모드: 스윙 (STABLE {settings.MAX_HOLD_DAYS_STABLE}일, "
                f"AGGRESSIVE {settings.MAX_HOLD_DAYS_AGGRESSIVE}일)"
            )

        return context

    async def _get_today_trade_stats(self) -> dict:
        """오늘 매매 승/패 집계 (trade_results 테이블)"""
        from models.trade_result import TradeResult
        from sqlalchemy import select, func
        from util.time_util import now_kst

        today = now_kst().date()
        stats = {"wins": 0, "losses": 0, "total": 0}
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(TradeResult.pnl).where(
                        func.date(TradeResult.created_at) == today
                    )
                )
                for (pnl,) in result:
                    stats["total"] += 1
                    if pnl >= 0:
                        stats["wins"] += 1
                    else:
                        stats["losses"] += 1
        except Exception:
            pass
        return stats

    def _apply_scan_thresholds(self, candidates: list[dict]) -> None:
        """시장 스캔 결과에서 AI가 결정한 모니터링 임계값을 event_detector에 적용

        각 candidate의 'monitoring' 필드에서 surge_pct, drop_pct, volume_spike_ratio를 가져와 설정.
        """
        applied = 0
        for c in candidates:
            symbol = c.get("symbol", "")
            monitoring = c.get("monitoring")
            if not symbol or not isinstance(monitoring, dict):
                continue

            kwargs = {}
            if "surge_pct" in monitoring:
                kwargs["surge_pct"] = float(monitoring["surge_pct"])
            if "drop_pct" in monitoring:
                kwargs["drop_pct"] = float(monitoring["drop_pct"])
            if "volume_spike_ratio" in monitoring:
                kwargs["volume_spike_ratio"] = float(monitoring["volume_spike_ratio"])

            if kwargs:
                event_detector.set_thresholds(symbol, **kwargs)
                applied += 1

        if applied:
            logger.debug("AI 모니터링 임계값 설정: {}종목", applied)

    def _apply_trade_thresholds(
        self, symbol: str, tier1: dict, tier2: dict,
    ) -> None:
        """Tier1/Tier2 분석 결과에서 손절/익절/트레일링 스탑을 event_detector에 적용

        Tier2 값을 우선 사용하고, 없으면 Tier1 값 사용.
        """
        kwargs = {}

        # stop_loss: Tier2 > Tier1
        stop_loss = tier2.get("stop_loss_price") or tier1.get("stop_loss_price")
        if stop_loss and float(stop_loss) > 0:
            kwargs["stop_loss"] = float(stop_loss)

        # take_profit: Tier2 target_price > Tier1 target_price
        take_profit = tier2.get("target_price") or tier1.get("target_price")
        if take_profit and float(take_profit) > 0:
            kwargs["take_profit"] = float(take_profit)

        # trailing_stop_pct: Tier2 > Tier1
        trailing = tier2.get("trailing_stop_pct") or tier1.get("trailing_stop_pct")
        if trailing and float(trailing) > 0:
            kwargs["trailing_stop_pct"] = float(trailing)

        if kwargs:
            event_detector.set_thresholds(symbol, **kwargs)
            logger.info(
                "AI 손절/익절 설정: {} → {}",
                symbol,
                ", ".join(f"{k}={v}" for k, v in kwargs.items()),
            )

    async def _get_today_trade_count(self) -> int:
        """당일 체결 건수 조회"""
        try:
            from models.order import Order
            from sqlalchemy import select, func
            from util.time_util import now_kst

            today = now_kst().date()
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(func.count(Order.id)).where(
                        func.date(Order.created_at) == today,
                        Order.status == "FILLED",
                    )
                )
                return result.scalar() or 0
        except Exception as e:
            logger.warning("당일 체결 건수 조회 실패: {}", str(e))
            return 0

    async def _tier1_analysis(
        self, symbol: str, name: str, current_price: float,
        chart_result: ChartAnalysisResult, price_data: dict,
        feedback_context: str = "",
        market_context: str = "",
        trading_context: str = "",
        cycle_id: str | None = None,
    ) -> dict | None:
        """Tier 1 AI 심층 분석"""
        prompt = STOCK_ANALYSIS_PROMPT.format(
            stock_name=name,
            symbol=symbol,
            current_price=current_price or 0,
            change=float(price_data.get("change") or 0),
            change_rate=float(price_data.get("change_rate") or 0),
            volume=int(float(price_data.get("volume") or 0)),
            technical_indicators=chart_result.indicators_text or "지표 데이터 없음",
            chart_patterns=chart_result.patterns_text or "차트 패턴 데이터 없음",
            daily_data=chart_result.trend_text or "추세 데이터 없음",
            per=price_data.get("per", "N/A"),
            pbr=price_data.get("pbr", "N/A"),
            market_cap=price_data.get("market_cap", "N/A"),
            feedback_context=feedback_context or "매매 이력 없음",
            market_context=market_context or "시장 컨텍스트 없음",
            trading_context=trading_context or "매매 컨텍스트 없음",
        )

        try:
            result_text, provider = await llm_factory.generate_tier1(
                prompt, system_prompt=STOCK_ANALYSIS_SYSTEM,
                symbol=symbol, cycle_id=cycle_id,
            )
            parsed = self._parse_json(result_text)
            if parsed:
                parsed["provider"] = provider
                parsed = self._validate_llm_prices(parsed, current_price)
            return parsed
        except Exception as e:
            logger.error("Tier 1 분석 실패 ({}): {}", symbol, str(e))
            return None

    async def _tier2_review(
        self, symbol: str, name: str, current_price: float,
        strategy_type: str, tier1_analysis: dict,
        feedback_context: str = "",
        chart_result: ChartAnalysisResult | None = None,
        dynamic_limits: dict | None = None,
        market_context: str = "",
        trading_context: str = "",
        portfolio_snapshot: dict | None = None,
        cycle_id: str | None = None,
    ) -> dict | None:
        """Tier 2 최종 검토"""
        strategy = self.strategies.get(strategy_type)
        snap = portfolio_snapshot or {}

        # 추세 분석 기반 전략 파라미터 조정 제안
        tuning_suggestions = "조정 제안 없음"
        if chart_result and chart_result.trend:
            trend = chart_result.trend
            suggestions = []
            if trend.direction == "BEARISH" and trend.strength == "STRONG":
                suggestions.append("강한 하락 추세 - 매수 진입 자제, 손절 타이트하게 설정 권장")
            if trend.momentum == "DECELERATING":
                suggestions.append("모멘텀 감속 중 - 진입 시점 재고 필요")
            if trend.volatility_state == "EXPANDING":
                suggestions.append("변동성 확대 구간 - 포지션 사이즈 축소 권장")
            if trend.volatility_state == "CONTRACTING":
                suggestions.append("변동성 수축 - 돌파 대기, 포지션 준비")
            if suggestions:
                tuning_suggestions = "\n".join(f"- {s}" for s in suggestions)

        # 포트폴리오 대비 비중 계산
        # max_single_order_krw=0이면 무제한 → 포지션 비중으로 산출
        max_order = dynamic_limits.get("max_single_order_krw", 0) if dynamic_limits else 0
        max_pos_pct = dynamic_limits.get("max_position_pct", 20.0) if dynamic_limits else 20.0
        total_asset = snap.get("total_asset", 0)
        max_amount = max_order if max_order > 0 else int(total_asset * max_pos_pct / 100) if total_asset > 0 else 0
        position_pct = (max_amount / total_asset * 100) if total_asset > 0 else 0

        prompt = FINAL_REVIEW_PROMPT.format(
            tier1_analysis=json.dumps(tier1_analysis, ensure_ascii=False, indent=2),
            stock_name=name,
            symbol=symbol,
            current_price=current_price or 0,
            strategy_type=strategy_type,
            max_amount=max_amount or 0,
            holding_count=snap.get("holding_count") or 0,
            position_pct=position_pct or 0,
            stop_loss_pct=getattr(strategy, "stop_loss_pct", None) or -3,
            take_profit_pct=getattr(strategy, "take_profit_pct", None) or 5,
            max_hold_days=5,
            max_position_pct=20,
            feedback_context=feedback_context or "매매 이력 없음",
            tuning_suggestions=tuning_suggestions,
            market_context=market_context or "시장 컨텍스트 없음",
            trading_context=trading_context or "매매 컨텍스트 없음",
        )

        try:
            result_text, provider = await llm_factory.generate_tier2(
                prompt, system_prompt=FINAL_REVIEW_SYSTEM,
                symbol=symbol, cycle_id=cycle_id,
            )
            parsed = self._parse_json(result_text)
            if parsed:
                parsed["provider"] = provider
                parsed = self._validate_llm_prices(parsed, current_price)
            return parsed
        except Exception as e:
            logger.error("Tier 2 검토 실패 ({}): {}", symbol, str(e))
            return None

    async def _on_market_event(self, event: Event) -> None:
        """실시간 시장 이벤트 → 즉시 해당 종목 분석/매매"""
        if not self._running:
            return

        # 장외 시간: 매매 불가이므로 이벤트 분석 스킵
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_krx_trading_hours():
            return

        # 데이트레이딩: 매수 마감 시간 이후 신규 매수 이벤트 무시
        if settings.DAY_TRADING_ONLY:
            from datetime import time as _dt_time
            from util.time_util import now_kst
            cutoff = _dt_time(settings.BUY_CUTOFF_HOUR, settings.BUY_CUTOFF_MINUTE)
            if now_kst().time() >= cutoff:
                return

        symbol = event.data.get("symbol", "")
        if not symbol:
            return

        # P2-8: 매도 진행 중인 종목 분석 스킵
        if symbol in self._selling:
            return

        # 쿨다운 체크 (동일 종목 연속 분석 방지)
        import time as _time
        now_ts = _time.time()
        last_ts = self._cooldowns.get(symbol, 0)
        if now_ts - last_ts < self.EVENT_COOLDOWN_SEC:
            return
        if symbol in self._analyzing:
            return

        self._cooldowns[symbol] = now_ts
        self._analyzing.add(symbol)

        price = event.data.get("price", 0)
        change_rate = event.data.get("change_rate", 0)
        event_type = event.type.value
        name = self._resolve_name(symbol)

        await activity_logger.log(
            ActivityType.EVENT, ActivityPhase.PROGRESS,
            f"\u26a1 실시간 감지: {event_type} - {name}({symbol}) "
            f"({price:,.0f}원, {change_rate:+.2f}%)",
            symbol=symbol,
            detail=event.data,
        )

        # 즉시 분석 + 매매 (비동기)
        try:
            # 실시간 이벤트에서도 트레이딩 컨텍스트 갱신
            self._trading_context = await self._build_trading_context()

            stock_info = {
                "symbol": symbol,
                "name": event.data.get("name") or self._resolve_name(symbol),
                "strategy_type": "AGGRESSIVE_SHORT" if abs(change_rate) >= 5 else "STABLE_SHORT",
                "trigger": event_type,
            }
            cycle_id = activity_logger.start_cycle()

            # 포트폴리오 스냅샷 조회 (리스크 체크용, MCP 1회)
            snapshot = {"cash": 0, "total_asset": 0, "holding_count": 0, "today_trade_count": 0}
            try:
                from trading.account_manager import account_manager
                balance, holdings = await account_manager.get_account_snapshot()
                if not balance.is_valid:
                    logger.error("실시간 이벤트: 계좌 조회 실패 → 분석 중단")
                    return
                snapshot["cash"] = balance.cash
                snapshot["total_asset"] = balance.total_asset
                snapshot["holding_count"] = len(holdings)
                snapshot["holding_symbols"] = [h.symbol for h in holdings]
                snapshot["today_trade_count"] = await self._get_today_trade_count()
            except Exception as e:
                logger.warning("실시간 이벤트 포트폴리오 스냅샷 조회 실패: {}", str(e))

            # 비보유종목 + 현금 부족 → 분석 스킵
            holding_syms = snapshot.get("holding_symbols", [])
            if symbol not in holding_syms and price > 0:
                if snapshot["cash"] < price:
                    logger.info(
                        "실시간 이벤트 스킵 (비보유 + 현금 부족): {} {:,.0f}원 < {:,.0f}원",
                        symbol, snapshot["cash"], price,
                    )
                    return

            # AI 한도
            dynamic_limits = None
            if settings.AI_RISK_TUNING_ENABLED:
                try:
                    from strategy.ai_risk_tuner import ai_risk_tuner
                    dynamic_limits = await ai_risk_tuner.compute_limits(
                        risk_appetite=settings.RISK_APPETITE, cycle_id=cycle_id,
                    )
                except Exception:
                    pass

            result = await self._analyze_and_trade(
                stock_info, cycle_id,
                dynamic_limits=dynamic_limits,
                portfolio_snapshot=snapshot,
            )
            if result.get("executed"):
                logger.info("실시간 매매 실행: {} ({})", symbol, event_type)
                # 신규 매수 종목 WebSocket 구독 추가
                await self._ensure_realtime_subscription(symbol)
        except Exception as e:
            logger.error("실시간 분석 오류 ({}): {}", symbol, str(e))
        finally:
            self._analyzing.discard(symbol)

    async def _on_stop_loss(self, event: Event) -> None:
        """손절선 도달 → 즉시 매도"""
        if not self._running:
            return
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_krx_trading_hours():
            return
        symbol = event.data.get("symbol", "")
        price = event.data.get("price", 0)
        stop_loss = event.data.get("stop_loss_price", 0)

        # P0-2: 이중 매도 방지
        if not await self._acquire_sell(symbol):
            return

        try:
            name = self._resolve_name(symbol)
            logger.warning("손절선 도달: {} {} (현재가: {:,.0f}, 손절: {:,.0f})", name, symbol, price, stop_loss)
            await activity_logger.log(
                ActivityType.EVENT, ActivityPhase.PROGRESS,
                f"\U0001f6a8 손절선 도달: {name}({symbol}) — 즉시 매도 실행 "
                f"(현재가: {price:,.0f}원, 손절: {stop_loss:,.0f}원)",
                symbol=symbol,
                detail=event.data,
            )

            # 즉시 시장가 매도
            if settings.TRADING_ENABLED:
                try:
                    from trading.account_manager import account_manager
                    holdings = await account_manager.get_holdings()
                    holding = next((h for h in holdings if h.symbol == symbol), None)
                    if holding and holding.quantity > 0:
                        # P2-8: 매도 전 이벤트 임계값 제거 (매도 실패 시 복원)
                        saved_thresholds = event_detector.get_thresholds(symbol)
                        event_detector.remove_levels(symbol)

                        resp = await mcp_client.place_order(
                            symbol=symbol, side="SELL",
                            quantity=holding.quantity, price=None, market="KRX",
                        )
                        await activity_logger.log(
                            ActivityType.ORDER, ActivityPhase.COMPLETE,
                            f"\U0001f6a8 손절 매도: {symbol} {holding.quantity}주 "
                            f"({'성공' if resp.success else '실패: ' + (resp.error or '')})",
                            symbol=symbol,
                        )
                        if resp.success:
                            # 체결 확인 + TradeResult 기록
                            order_data = resp.data or {}
                            order_id = order_data.get("order_id", "")
                            await decision_maker.confirm_and_record(
                                symbol=symbol,
                                side="SELL",
                                order_id=order_id,
                                quantity=holding.quantity,
                                expected_price=price,
                                exit_reason="STOP_LOSS",
                            )
                        else:
                            # 매도 실패 → 임계값 복원
                            if saved_thresholds.stop_loss > 0 or saved_thresholds.take_profit > 0:
                                kwargs = {}
                                if saved_thresholds.stop_loss > 0:
                                    kwargs["stop_loss"] = saved_thresholds.stop_loss
                                if saved_thresholds.take_profit > 0:
                                    kwargs["take_profit"] = saved_thresholds.take_profit
                                event_detector.set_thresholds(symbol, **kwargs)
                except Exception as e:
                    logger.error("손절 매도 실패 ({}): {}", symbol, str(e))
        finally:
            self._release_sell(symbol)
            # 손절 후 30분간 재진입 차단 (무한 매수-손절 루프 방지)
            import time as _time
            self._cooldowns[symbol] = _time.time() + self.EXIT_COOLDOWN_SEC
            from agent.market_scanner import market_scanner
            market_scanner.add_untradeable(symbol)
            logger.debug("[{}] 손절 후 {}분 재진입 차단 설정", symbol, self.EXIT_COOLDOWN_SEC // 60)

    async def _on_take_profit(self, event: Event) -> None:
        """익절선 도달 → 즉시 매도"""
        if not self._running:
            return
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_krx_trading_hours():
            return
        symbol = event.data.get("symbol", "")
        price = event.data.get("price", 0)
        take_profit = event.data.get("take_profit_price", 0)

        # P0-2: 이중 매도 방지
        if not await self._acquire_sell(symbol):
            return

        try:
            name = self._resolve_name(symbol)
            logger.info("익절선 도달: {} {} (현재가: {:,.0f}, 익절: {:,.0f})", name, symbol, price, take_profit)
            await activity_logger.log(
                ActivityType.EVENT, ActivityPhase.PROGRESS,
                f"\U0001f3af 익절선 도달: {name}({symbol}) — 매도 실행 "
                f"(현재가: {price:,.0f}원, 익절: {take_profit:,.0f}원)",
                symbol=symbol,
                detail=event.data,
            )

            # 즉시 시장가 매도
            if settings.TRADING_ENABLED:
                try:
                    from trading.account_manager import account_manager
                    holdings = await account_manager.get_holdings()
                    holding = next((h for h in holdings if h.symbol == symbol), None)
                    if holding and holding.quantity > 0:
                        # P2-8: 매도 전 이벤트 임계값 제거 (매도 실패 시 복원)
                        saved_thresholds = event_detector.get_thresholds(symbol)
                        event_detector.remove_levels(symbol)

                        resp = await mcp_client.place_order(
                            symbol=symbol, side="SELL",
                            quantity=holding.quantity, price=None, market="KRX",
                        )
                        await activity_logger.log(
                            ActivityType.ORDER, ActivityPhase.COMPLETE,
                            f"\U0001f3af 익절 매도: {symbol} {holding.quantity}주 "
                            f"({'성공' if resp.success else '실패: ' + (resp.error or '')})",
                            symbol=symbol,
                        )
                        if resp.success:
                            # 체결 확인 + TradeResult 기록
                            order_data = resp.data or {}
                            order_id = order_data.get("order_id", "")
                            await decision_maker.confirm_and_record(
                                symbol=symbol,
                                side="SELL",
                                order_id=order_id,
                                quantity=holding.quantity,
                                expected_price=price,
                                exit_reason="TAKE_PROFIT",
                            )
                        else:
                            # 매도 실패 → 임계값 복원
                            if saved_thresholds.stop_loss > 0 or saved_thresholds.take_profit > 0:
                                kwargs = {}
                                if saved_thresholds.stop_loss > 0:
                                    kwargs["stop_loss"] = saved_thresholds.stop_loss
                                if saved_thresholds.take_profit > 0:
                                    kwargs["take_profit"] = saved_thresholds.take_profit
                                event_detector.set_thresholds(symbol, **kwargs)
                except Exception as e:
                    logger.error("익절 매도 실패 ({}): {}", symbol, str(e))
        finally:
            self._release_sell(symbol)
            # 익절 후에도 재진입 차단 (같은 가격대 재매수 방지)
            import time as _time
            self._cooldowns[symbol] = _time.time() + self.EXIT_COOLDOWN_SEC
            from agent.market_scanner import market_scanner
            market_scanner.add_untradeable(symbol)
            logger.debug("[{}] 익절 후 {}분 재진입 차단 설정", symbol, self.EXIT_COOLDOWN_SEC // 60)

    async def _ensure_realtime_subscription(self, symbol: str) -> None:
        """매수 후 WebSocket 실시간 구독 확인/추가"""
        try:
            from realtime.stream_manager import stream_manager
            await stream_manager.subscribe_symbols([(symbol, "KRX")])
            logger.debug("매수 종목 WebSocket 구독 추가: {}", symbol)
        except Exception as e:
            logger.warning("WebSocket 구독 추가 실패 ({}): {}", symbol, str(e))

    @staticmethod
    def _validate_llm_prices(analysis: dict, current_price: float) -> dict:
        """LLM 응답의 가격/신뢰도 값을 검증하고 보정

        - target_price: (current_price * 0.5, current_price * 2.0) 범위
        - stop_loss_price: (current_price * 0.5, current_price) 범위
        - stop_loss < target_price 검증
        - confidence: [0.0, 1.0] 클램핑 (100 초과 시 /100)
        """
        if not analysis or current_price <= 0:
            return analysis or {}

        # confidence 검증
        try:
            conf = float(analysis.get("confidence", 0))
            if conf > 1.0:
                conf = conf / 100.0 if conf <= 100.0 else 1.0
            conf = max(0.0, min(1.0, conf))
            analysis["confidence"] = conf
        except (TypeError, ValueError):
            analysis["confidence"] = 0.0

        # target_price 검증
        try:
            tp = analysis.get("target_price")
            if tp is not None:
                tp = float(tp)
                if tp <= 0 or tp < current_price * 0.5 or tp > current_price * 2.0:
                    logger.warning("LLM target_price 범위 초과: {} (현재가: {})", tp, current_price)
                    analysis["target_price"] = None
                else:
                    analysis["target_price"] = tp
        except (TypeError, ValueError):
            analysis["target_price"] = None

        # stop_loss_price 검증
        try:
            sl = analysis.get("stop_loss_price")
            if sl is not None:
                sl = float(sl)
                if sl <= 0 or sl < current_price * 0.5 or sl >= current_price:
                    logger.warning("LLM stop_loss_price 범위 초과: {} (현재가: {})", sl, current_price)
                    analysis["stop_loss_price"] = None
                else:
                    analysis["stop_loss_price"] = sl
        except (TypeError, ValueError):
            analysis["stop_loss_price"] = None

        # stop_loss < target_price 교차 검증
        tp = analysis.get("target_price")
        sl = analysis.get("stop_loss_price")
        if tp is not None and sl is not None and sl >= tp:
            logger.warning("LLM stop_loss({}) >= target_price({}) → 둘 다 무효화", sl, tp)
            analysis["target_price"] = None
            analysis["stop_loss_price"] = None

        return analysis

    def _parse_json(self, text: str) -> dict | None:
        from core.json_utils import parse_llm_json
        result = parse_llm_json(text)
        return result if result else None

    @property
    def last_cycle_time(self):
        return self._last_cycle_time


trading_agent = TradingAgent()
