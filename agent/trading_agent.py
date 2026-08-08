"""AI Trading Agent 메인 루프 - 장중: 스캔→판단→분석→매매 / 장외: 성과 리뷰→피드백 학습"""
import asyncio
import json

from loguru import logger

from agent.decision_maker import decision_maker
from agent.market_scanner import market_scanner
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.daily_plan import DAILY_PLAN_PROMPT, DAILY_PLAN_SYSTEM
from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, EventType, event_bus
from realtime.event_detector import event_detector
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from strategy.aggressive_short import AggressiveShortStrategy
from strategy.stable_short import StableShortStrategy
from trading.enums import ActivityPhase, ActivityType
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
        self._cooldowns: dict[str, float] = {}  # symbol -> last_trigger_time (이벤트 분석)
        self._exit_cooldowns: dict[str, float] = {}  # symbol -> 매도 시각 (재진입 차단)
        self._symbol_locks: dict[str, asyncio.Lock] = {}  # 종목별 lock (사이클·실시간 race 방지)
        self._emergency_stop_until: float = 0.0  # 시장 급락 emergency stop 만료 시각 (epoch)
        self.EVENT_COOLDOWN_SEC = 120  # 동일 종목 재분석 최소 간격 (초)
        self.EXIT_COOLDOWN_SEC = 1800  # 손절/익절 후 재진입 차단 (30분, 손절·익절 통합)
        # 사이클 내 시장 컨텍스트 캐시 (Tier1/Tier2에 전달)
        self._market_context: str = ""
        # 시장 국면 (전략/리스크에 전달)
        self._market_regime: str = ""
        # AI Risk Tuner가 결정한 동적 한도 (Admin API / buy_agent 참조)
        self._dynamic_limits: dict = {}
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
        # 연속 손실 일시정지
        self._loss_pause_until: float = 0.0

    async def start(self) -> None:
        """에이전트 시작 — 각 Agent에 위임

        - MarketRegimeAgent: 국면 감시 + 실시간 신호 라우팅
        - SellAgent: 손절/익절 안전장치 + 매도
        - BuyAgent: 매수 분석 + 실행
        """
        self._running = True
        from agent.sell_agent import sell_agent
        from agent.buy_agent import buy_agent
        await sell_agent.start()
        await buy_agent.start()

        # 국면 변화 콜백 등록 — 변화 감지 시 보유종목 즉시 재평가
        from agent.market_regime_agent import market_regime_agent
        market_regime_agent.set_regime_change_callback(self._on_regime_changed)

        logger.debug("AI Trading Agent 시작 — SellAgent/BuyAgent 활성화 + 국면 콜백 등록")

    async def _on_regime_changed(self, new_regime: str, old_regime: str) -> None:
        """국면 변화 감지 시 보유종목 즉시 Tier2 재분석 트리거.

        손절선 조정/매도 결정이 다음 사이클까지 늦어지지 않도록 즉시 처리.
        """
        try:
            from trading.account_manager import account_manager
            from agent.stock_analysis_agent import StockAnalysisRequest, stock_analysis_agent
            from realtime.event_detector import event_detector

            holdings = await account_manager.get_holdings()
            if not holdings:
                logger.info("국면 변화({}→{}) → 보유종목 없음, 재평가 스킵", old_regime, new_regime)
                return

            logger.info(
                "국면 변화({}→{}) → 보유 {}종목 즉시 재평가 트리거",
                old_regime, new_regime, len(holdings),
            )

            async def _reanalyze(h):
                try:
                    th = event_detector.get_thresholds(h.symbol)
                    request = StockAnalysisRequest(
                        symbol=h.symbol, name=h.name, strategy_type="STABLE_SHORT",
                        purpose="REGIME_CHANGE", is_holding=True,
                        avg_price=h.avg_buy_price, pnl_rate=h.pnl_rate, quantity=h.quantity,
                        active_stop_loss=getattr(th, "stop_loss", 0) or 0,
                        active_take_profit=getattr(th, "take_profit", 0) or 0,
                        active_trailing_stop_pct=getattr(th, "trailing_stop_pct", 0) or 0,
                    )
                    await stock_analysis_agent.analyze(request, force=True)
                except Exception as e:
                    logger.warning("[{}] 국면 변화 재평가 실패: {}", h.symbol, str(e))

            await asyncio.gather(*[_reanalyze(h) for h in holdings], return_exceptions=True)
        except Exception as e:
            logger.warning("국면 변화 콜백 처리 실패: {}", str(e))

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

    def set_exit_cooldown(self, symbol: str) -> None:
        """매도 성공 시 호출 — 손절·익절 모두 동일 cooldown 적용 (재진입 차단)"""
        import time as _time
        self._exit_cooldowns[symbol] = _time.time()
        logger.info(
            "[{}] 매도 후 재진입 cooldown 시작 ({}분)",
            symbol, self.EXIT_COOLDOWN_SEC // 60,
        )

    def is_in_exit_cooldown(self, symbol: str) -> bool:
        """매도 후 cooldown 중인지 확인 (만료 시 자동 정리)"""
        import time as _time
        last = self._exit_cooldowns.get(symbol)
        if last is None:
            return False
        elapsed = _time.time() - last
        if elapsed < self.EXIT_COOLDOWN_SEC:
            return True
        # 만료 → 자동 정리
        self._exit_cooldowns.pop(symbol, None)
        return False

    def get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        """종목별 lock — 사이클 분석/실시간 손절/매수/매도 모두 같은 lock 거침
        Race condition 방지: 분석 중 손절 트리거되면 분석 끝날 때까지 대기.
        """
        if symbol not in self._symbol_locks:
            self._symbol_locks[symbol] = asyncio.Lock()
        return self._symbol_locks[symbol]

    def _resolve_name(self, symbol: str) -> str:
        """종목코드 → 종목명 반환 (캐시에 없으면 코드 그대로)"""
        return self._symbol_names.get(symbol, symbol)

    async def run_cycle(self) -> dict:
        """에이전트 1회 실행 사이클 — 장중이면 매매, 장외면 리뷰"""
        if self._cycle_lock.locked():
            logger.warning("사이클 이미 실행 중 — 중복 트리거 무시")
            return {"skipped": True, "reason": "cycle_already_running"}

        async with self._cycle_lock:
            if market_calendar.is_domestic_trading_hours():
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

        session = market_calendar.get_market_session()
        session_label = {"NXT_PRE": "NXT 프리마켓", "NXT_AFTER": "NXT 애프터마켓", "KRX_NXT": "장중", "KRX_CLOSE": "종가경매"}.get(session, "장중")
        logger.info("=== Agent {} 사이클 시작 ===", session_label)
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
        try:
            from strategy.ai_risk_tuner import ai_risk_tuner
            dynamic_limits = await ai_risk_tuner.compute_limits(
                cycle_id=cycle_id,
            )
        except Exception as e:
            logger.warning("AI 한도 결정 실패, 기본값 사용: {}", str(e))
        # 싱글톤 속성에 저장 → Admin API, buy_agent 등에서 참조
        self._dynamic_limits = dynamic_limits or {}

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

            buy_blocked = False

            # ── 시장 급락 emergency stop: KOSPI/KOSDAQ -3% 이상 → 매수 1시간 차단 ──
            try:
                from agent.market_regime_agent import market_regime_agent
                kospi_rate = market_regime_agent._last_kospi.get("change_rate", 0) if market_regime_agent._last_kospi.get("success") else 0
                kosdaq_rate = market_regime_agent._last_kosdaq.get("change_rate", 0) if market_regime_agent._last_kosdaq.get("success") else 0
                worst = min(kospi_rate, kosdaq_rate)
                if worst <= -3.0:
                    import time as _t
                    self._emergency_stop_until = _t.time() + 3600  # 1시간 차단
                    logger.warning(
                        "🚨 시장 급락 emergency stop: KOSPI {:.2f}% / KOSDAQ {:.2f}% → 매수 1시간 차단",
                        kospi_rate, kosdaq_rate,
                    )
                    await activity_logger.log(
                        ActivityType.CYCLE, ActivityPhase.PROGRESS,
                        f"🚨 시장 급락 매수 차단: KOSPI {kospi_rate:+.2f}% / KOSDAQ {kosdaq_rate:+.2f}% "
                        f"→ 매수 1시간 차단 (보유종목 매도는 정상)",
                        cycle_id=cycle_id,
                    )
            except Exception:
                pass

            # emergency stop 미만료 시 매수 차단
            import time as _t
            if _t.time() < self._emergency_stop_until:
                buy_blocked = True
                remaining_sec = int(self._emergency_stop_until - _t.time())
                logger.info("emergency stop 활성: 매수 차단 ({}초 남음)", remaining_sec)

            # ── 서킷브레이커: 일일 손실 한도 ──
            daily_pnl_pct = 0.0
            if self._daily_start_balance > 0 and snapshot["total_asset"] > 0:
                daily_pnl_pct = (
                    (snapshot["total_asset"] - self._daily_start_balance)
                    / self._daily_start_balance * 100
                )

            # ── 시스템 하드 리밋: -7% → 전체 매매 즉시 중단 (AI도 무시 못함) ──
            if daily_pnl_pct <= settings.DAILY_LOSS_LIMIT_HARD:
                logger.warning(
                    "시스템 하드 리밋 발동: 일일 손실 {:.2f}% ≤ {:.1f}% → 전체 매매 중단",
                    daily_pnl_pct, settings.DAILY_LOSS_LIMIT_HARD,
                )
                await activity_logger.log(
                    ActivityType.CYCLE, ActivityPhase.COMPLETE,
                    f"\U0001f6d1 시스템 하드 리밋: 일일 손실 {daily_pnl_pct:+.2f}% "
                    f"→ 전체 매매 중단 (한도 {settings.DAILY_LOSS_LIMIT_HARD}%)",
                    cycle_id=cycle_id,
                )
                return results
            # 소프트 리밋/연속 손실 판단은 AI Risk Tuner가 동적 결정

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
                    ActivityType.CYCLE, ActivityPhase.PROGRESS,
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
            # MarketRegimeAgent에 스캔 결과 국면 반영
            from agent.market_regime_agent import market_regime_agent
            if self._market_regime:
                market_regime_agent.set_regime(self._market_regime)

            # 1c. 데이트레이딩 컨텍스트 빌드 (시간/손익/매매성적)
            self._trading_context = await self._build_trading_context()

            # 선정 종목을 결과에 저장 (WebSocket 구독용)
            active_market = market_calendar.get_active_market()
            results["selected_symbols"] = [
                (c.get("symbol", ""), active_market)
                for c in candidates if c.get("symbol")
            ]

            # 2. 후보 종목별 분석(StockAnalysisAgent) → 결과에 따라 라우팅 (병렬)
            # 주: _apply_scan_thresholds 제거 — PriceGuard는 매수 체결 종목만 등록
            from agent.buy_agent import buy_agent
            from agent.sell_agent import sell_agent
            from agent.stock_analysis_agent import StockAnalysisRequest, stock_analysis_agent

            # Claude Code 세션 일시 중지 → 병렬 분석 독립 호출
            paused_sid = ClaudeCodeProvider.pause_session()

            semaphore = asyncio.Semaphore(3)
            holding_syms = set(snapshot.get("holding_symbols", []))

            import time as _time

            def _route_by_result(analysis, sym, is_held):
                """분석 결과에 따라 라우팅 (캐시 히트 시에도 사용)"""
                rec = analysis.recommendation
                if rec == "BUY":
                    return {"symbol": sym, "signal": True, "executed": False, "route": "buy", "cached": True}
                elif rec == "SELL" and is_held:
                    return {"symbol": sym, "signal": True, "executed": False, "route": "sell", "cached": True}
                elif rec == "HOLD" and is_held:
                    return {"symbol": sym, "signal": False, "executed": False, "route": "hold", "cached": True}
                return {"symbol": sym, "signal": False, "executed": False, "cached": True}

            async def _analyze_and_route(stock_info: dict) -> dict:
                async with semaphore:
                    symbol = stock_info.get("symbol", "")
                    name = stock_info.get("name", symbol)
                    strategy_type = stock_info.get("strategy_type", "STABLE_SHORT")
                    is_holding = symbol in holding_syms

                    # 비보유 + 매수 차단 → 스킵
                    if not is_holding and buy_blocked:
                        return {"symbol": symbol, "skipped": True, "reason": "매수 차단"}

                    # 비보유 + 매도 후 cooldown 중 → 스킵 (손절·익절 후 재진입 차단)
                    if not is_holding and self.is_in_exit_cooldown(symbol):
                        return {"symbol": symbol, "skipped": True, "reason": f"매도 cooldown ({self.EXIT_COOLDOWN_SEC // 60}분)"}

                    # 비보유 + 주문가능금액으로 1주도 못 사는 종목 → 분석 스킵 (LLM 비용 절감)
                    if not is_holding:
                        try:
                            _price_resp = await mcp_client.get_current_price(symbol)
                            _stock_price = float((_price_resp.data or {}).get("price", 0)) if _price_resp.success else 0
                            if _stock_price > 0:
                                from trading.kis_api import get_buying_power
                                _bp = await get_buying_power(symbol, price=int(_stock_price))
                                _avail = _bp.get("available_cash", snapshot.get("cash", 0)) if _bp.get("success") else snapshot.get("cash", 0)
                                if _avail < _stock_price:
                                    # 예수금 총액 - 주문가능금액 = 미체결/결제대기/증거금 등으로 잠긴 금액
                                    _total_cash = float(snapshot.get("cash", 0))
                                    _locked = max(0, _total_cash - float(_avail))
                                    if _locked > 0:
                                        logger.info(
                                            "[{}] 주문가능금액 부족으로 분석 스킵: 주문가능 {:,.0f}원 < 주가 {:,.0f}원/주 "
                                            "(예수금 {:,.0f}원 중 {:,.0f}원이 미체결 주문·결제대기 등으로 잠김)",
                                            symbol, _avail, _stock_price, _total_cash, _locked,
                                        )
                                    else:
                                        logger.info(
                                            "[{}] 주문가능금액 부족으로 분석 스킵: {:,.0f}원 < {:,.0f}원/주",
                                            symbol, _avail, _stock_price,
                                        )
                                    return {"symbol": symbol, "skipped": True, "reason": f"주문가능금액 부족 ({_avail:,.0f} < {_stock_price:,.0f})"}
                        except Exception:
                            pass

                    # 기존 분석 결과 확인 (중복 분석 방지)
                    cached = stock_analysis_agent.get_result(symbol)
                    if cached and cached.success:
                        from agent.market_regime_agent import market_regime_agent
                        elapsed = _time.time() - cached.analyzed_at
                        # 국면 변화 검사 — 분석 시점 국면 != 현재면 캐시 무효화 (강제 재분석)
                        current_regime = market_regime_agent.current_regime or ""
                        if cached.analyzed_regime and cached.analyzed_regime != current_regime:
                            logger.info(
                                "[{}] 국면 변화 감지 ({} → {}) → 캐시 무효, 재분석",
                                symbol, cached.analyzed_regime, current_regime,
                            )
                            stock_analysis_agent.invalidate(symbol)
                        elif elapsed < market_regime_agent.scan_interval_sec:
                            logger.debug("[{}] 기존 분석 결과 사용 ({:.0f}초 전)", symbol, elapsed)
                            return _route_by_result(cached, symbol, is_holding)

                    # StockAnalysisAgent로 통합 분석 (보유/비보유 동일 파이프라인)
                    holding_info = {}
                    if is_holding:
                        from trading.account_manager import account_manager
                        h_list = await account_manager.get_holdings()
                        h = next((x for x in h_list if x.symbol == symbol), None)
                        if h:
                            th = event_detector.get_thresholds(symbol)
                            holding_info = {
                                "is_holding": True,
                                "avg_price": h.avg_buy_price,
                                "pnl_rate": h.pnl_rate,
                                "quantity": h.quantity,
                                "active_stop_loss": th.stop_loss,
                                "active_take_profit": th.take_profit,
                                "active_trailing_stop_pct": th.trailing_stop_pct,
                            }

                    request = StockAnalysisRequest(
                        symbol=symbol,
                        name=name,
                        strategy_type=strategy_type,
                        purpose="PERIODIC_REVIEW" if is_holding else "NEW_BUY",
                        market_context=self._market_context,
                        trading_context=self._trading_context,
                        cycle_id=cycle_id,
                        **holding_info,
                    )

                    analysis = await stock_analysis_agent.analyze(request)
                    if not analysis.success:
                        return {"symbol": symbol, "signal": False, "executed": False}

                    # 분석 결과에 따라 라우팅 — 실행에 필요한 값만 전달
                    from agent.buy_agent import BuyParams
                    from agent.sell_agent import SellParams

                    rec = analysis.recommendation
                    if rec == "BUY":
                        _max_pos = dynamic_limits.get("max_position_pct", 20.0) if dynamic_limits else 20.0
                        r = await buy_agent.execute(BuyParams(
                            symbol=symbol, name=name, strategy_type=strategy_type,
                            price=analysis.current_price, confidence=analysis.confidence,
                            reason=analysis.reason,
                            max_position_pct=_max_pos,
                            stop_loss_price=analysis.stop_loss_price,
                            take_profit_price=analysis.target_price,
                            trailing_stop_pct=analysis.trailing_stop_pct,
                            breakeven_trigger_pct=analysis.breakeven_trigger_pct,
                            review_threshold_pct=analysis.review_threshold_pct,
                            review_interval_min=analysis.review_interval_min,
                        ))
                        return {**r, "signal": True, "route": "buy"}
                    elif rec == "SELL" and is_holding:
                        await sell_agent.execute_sell(SellParams(symbol=symbol, exit_reason="ANALYSIS_SELL"))
                        return {"symbol": symbol, "signal": True, "executed": True, "route": "sell"}
                    elif rec == "HOLD" and is_holding:
                        # 임계값은 StockAnalysisAgent가 분석 시 직접 설정 완료
                        return {"symbol": symbol, "signal": False, "executed": False, "route": "hold"}
                    else:
                        return {"symbol": symbol, "signal": False, "executed": False}

            all_results = await asyncio.gather(
                *[_analyze_and_route(s) for s in candidates],
                return_exceptions=True,
            )

            # 병렬 분석 완료 → 세션 재개
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

        logger.info("=== Agent {} 사이클 종료: {} ===", session_label, results)
        return results

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

            # 2. 포트폴리오 현황 (장 마감 리뷰는 최신 데이터 필요 → 캐시 무효화)
            account_manager.invalidate_cache()
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

            # 3-1. 체결 확인 백그라운드 태스크 완료 대기 (실현 손익 정확성 보장)
            awaited = await decision_maker.await_pending_tasks()
            if awaited:
                logger.info("체결 확인 {}건 완료 → 매매 내역 조회 진행", awaited)

            # 4. 오늘 실제 매매 내역 조회 (TradeResult 기반)
            today_trades_text = "매매 내역 없음"
            today_buy_count = 0
            today_sell_count = 0
            today_win_count = 0
            today_loss_count = 0
            today_realized_pnl = 0.0
            today_open_position_count = 0

            try:
                async with AsyncSessionLocal() as session:
                    from repositories.trade_result_repository import TradeResultRepository
                    trade_repo = TradeResultRepository(session)

                    opened_trades = await trade_repo.get_opened_by_date(today_date)
                    completed_trades = await trade_repo.get_completed_by_date(today_date)
                    all_open = await trade_repo.get_all_open()

                    today_buy_count = len(opened_trades)
                    today_sell_count = len(completed_trades)
                    today_win_count = sum(1 for t in completed_trades if t.is_win)
                    today_loss_count = sum(1 for t in completed_trades if not t.is_win)
                    today_realized_pnl = sum(t.pnl for t in completed_trades)
                    today_open_position_count = len({t.stock_symbol for t in all_open})

                    lines = []

                    if opened_trades:
                        lines.append("#### 오늘 매수")
                        for t in opened_trades:
                            status = "보유 중" if t.exit_at is None else "청산 완료"
                            conf = t.ai_confidence or 0.0
                            lines.append(
                                f"- {t.stock_name}({t.stock_symbol}): "
                                f"매수가 {t.entry_price:,.0f}원 × {t.quantity}주, "
                                f"전략 {t.strategy_type}, 신뢰도 {conf:.2f}, "
                                f"상태: {status}"
                            )

                    if completed_trades:
                        lines.append("#### 오늘 청산 (실현 손익)")
                        for t in completed_trades:
                            win_mark = "✅" if t.is_win else "❌"
                            lines.append(
                                f"- {win_mark} {t.stock_name}({t.stock_symbol}): "
                                f"매수 {t.entry_price:,.0f}원 → 매도 {t.exit_price:,.0f}원, "
                                f"{t.quantity}주, 손익 {t.pnl:+,.0f}원 ({t.return_pct:+.2f}%), "
                                f"보유 {t.hold_days}일, 사유: {t.exit_reason}"
                            )
                        lines.append(
                            f"\n**오늘 실현 손익 합계: {today_realized_pnl:+,.0f}원** "
                            f"(승 {today_win_count}건 / 패 {today_loss_count}건)"
                        )

                    if lines:
                        today_trades_text = "\n".join(lines)

            except Exception as e:
                logger.warning("오늘 매매 내역 조회 실패: {}", str(e))

            # 5-1. 과거 매매 성과
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

            # 5-1-1. 판단 검증 실행 + 롤링 통계·적중률 텍스트 (하네스 채점 결과 주입)
            rolling_stats_text = "데이터 없음"
            judgment_accuracy_text = "아직 채점된 판단 없음"
            rule_verification_text = "검증된 규칙 없음"
            try:
                from analysis.feedback.judgment_verifier import judgment_verifier

                # 오늘 판단 채점 (일봉 저장 포함) — 리뷰 직전 최신화
                await judgment_verifier.verify_all()

                stats = await judgment_verifier.get_accuracy_stats()
                judgment_accuracy_text = judgment_verifier.format_stats_for_prompt(stats)
                # 조기매도로 놓친 상승 요약 (있을 때만 이어붙임)
                missed = await judgment_verifier.get_peak_sell_missed_summary()
                if missed:
                    judgment_accuracy_text = f"{judgment_accuracy_text}\n{missed}"

                async with AsyncSessionLocal() as session:
                    from repositories.trade_result_repository import TradeResultRepository
                    breakdown = await TradeResultRepository(session).get_rolling_breakdown(days=21)

                if breakdown["total"]["n"] > 0:
                    _lines = [
                        f"전체: {breakdown['total']['n']}건, 승률 {breakdown['total']['win_rate']}%, "
                        f"손익 {breakdown['total']['pnl']:+,.0f}원, 평균수익률 {breakdown['total']['avg_return']:+.2f}%",
                    ]
                    for title, key in (("전략별", "by_strategy"), ("보유시간별", "by_hold_time"),
                                       ("진입 신뢰도별", "by_confidence")):
                        _lines.append(f"[{title}]")
                        for bucket, s in sorted(breakdown[key].items()):
                            _lines.append(
                                f"  - {bucket}: {s['n']}건, 승률 {s['win_rate']}%, "
                                f"손익 {s['pnl']:+,.0f}원, 평균 {s['avg_return']:+.2f}%"
                            )
                    rolling_stats_text = "\n".join(_lines)

                # 직전 규칙 검증 결과 (최근 7일 채점분)
                from datetime import timedelta as _td
                from models import JudgmentVerification
                from sqlalchemy import select as _select
                async with AsyncSessionLocal() as session:
                    rule_jvs = (await session.execute(
                        _select(JudgmentVerification)
                        .where(JudgmentVerification.judgment_type == "TRADING_RULE")
                        .where(JudgmentVerification.verified_at >= now_kst() - _td(days=7))
                        .order_by(JudgmentVerification.verified_at.desc())
                        .limit(10)
                    )).scalars().all()
                if rule_jvs:
                    _rl = []
                    for jv in rule_jvs:
                        mark = {"CORRECT": "✅ 효과 있음", "WRONG": "❌ 역효과",
                                "EXPIRED": "➖ 판정 불가(표본 부족/차이 없음)"}.get(jv.verdict, jv.verdict)
                        _rl.append(f"- {jv.rationale[:80]} → {mark}")
                    rule_verification_text = "\n".join(_rl)
            except Exception as e:
                logger.warning("판단 검증 통계 생성 실패: {}", str(e))

            # 5-2. 오버나이트 보유종목 현황
            overnight_holdings_text = "없음"
            if True:  # AI가 hold_strategy 판단하므로 항상 체크
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
                                    # 매수 체결 실체 검증 — 미체결 주문을 "체결된 것"으로
                                    # 간주해 허위 손익을 만드는 사고 방지 (KB금융 -33,600원)
                                    fill = await decision_maker.verify_buy_fill(
                                        tr.order_id, tr.stock_symbol, tr.entry_at,
                                    )
                                    if fill is not None and fill["qty"] <= 0:
                                        tr.status = "CONFIRM_FAILED"
                                        tr.notes = "ORPHAN_CLEANUP: KIS 매수 체결 실체 없음 → 손익 미계상"
                                        orphan_count += 1
                                        logger.warning(
                                            "[{}] 고아 레코드 — KIS 체결 실체 없음 → CONFIRM_FAILED 마킹 (주문 {})",
                                            tr.stock_symbol, tr.order_id,
                                        )
                                        continue
                                    if fill and fill["qty"] > 0 and fill["qty"] != tr.quantity:
                                        logger.warning(
                                            "[{}] 고아 레코드 수량 보정: {} → {}주 (KIS 실체결)",
                                            tr.stock_symbol, tr.quantity, fill["qty"],
                                        )
                                        tr.quantity = fill["qty"]
                                        if fill.get("price"):
                                            tr.entry_price = fill["price"]

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
                                        from util.time_util import ensure_kst
                                        tr.hold_days = (now - ensure_kst(tr.entry_at)).days if tr.entry_at else 0

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

            trading_mode_text = (
                "AI 자율 판단 모드: 종목별로 당일 청산(DAY_CLOSE) 또는 오버나이트 보유(OVERNIGHT)를 "
                "AI가 추세/모멘텀/국면 기반으로 판단. "
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
                today_trades_text=today_trades_text,
                today_buy_count=today_buy_count,
                today_sell_count=today_sell_count,
                today_realized_pnl=today_realized_pnl,
                activity_summary=activity_summary,
                performance_summary=performance_summary,
                rolling_stats_text=rolling_stats_text,
                judgment_accuracy_text=judgment_accuracy_text,
                rule_verification_text=rule_verification_text,
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
                        buy_count=today_buy_count,
                        sell_count=today_sell_count,
                        win_count=today_win_count,
                        loss_count=today_loss_count,
                        total_pnl=today_realized_pnl,
                        unrealized_pnl=balance.total_pnl,
                        open_position_count=today_open_position_count,
                    )
                except Exception as e:
                    logger.warning("일일 리포트 저장 실패: {}", str(e))

                # 일일 리뷰 → 트레이딩 규칙 자동 생성 (내일 코드 레벨 강제 적용)
                # 안전장치: 분석/매매 0건은 비정상 상태(시스템 미가동·자정 넘김 등) →
                # LLM이 추측으로 만든 PARAM_OVERRIDE는 잘못된 보정이라 무조건 skip
                try:
                    from analysis.feedback.trading_rules import trading_rule_engine
                    if today_analyses == 0 and today_orders == 0:
                        await activity_logger.log(
                            ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                            f"⚠️ 트레이딩 규칙 생성 skip — 분석 0건 + 매매 0건 (비정상 상태, "
                            f"LLM 추측 기반 룰 생성 위험)",
                            cycle_id=cycle_id,
                        )
                        rules = []
                    else:
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
        buy_count: int = 0, sell_count: int = 0,
        win_count: int = 0, loss_count: int = 0,
        total_pnl: float = 0.0, unrealized_pnl: float = 0.0,
        open_position_count: int = 0,
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
                    "buy_count": buy_count,
                    "sell_count": sell_count,
                    "win_count": win_count,
                    "loss_count": loss_count,
                    "total_pnl": total_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "open_position_count": open_position_count,
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

        # 세션 정보 (NXT 특성 포함)
        session = market_calendar.get_market_session()
        if session == "NXT_PRE":
            parts.append("거래소: NXT 프리마켓 (08:00~08:50)")
            parts.append("NXT 특성: 유동성 낮음, 단일가 매매, 전일 뉴스/공시 반영 포지셔닝 구간")
        elif session == "NXT_AFTER":
            parts.append("거래소: NXT 애프터마켓 (15:30~20:00)")
            parts.append("NXT 특성: 유동성 낮음, 단일가 매매, 장중 미반영 뉴스/공시 대응 + 다음날 선취매 구간")

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

        # 세션별 마감 시간
        session = market_calendar.get_market_session()
        if session == "NXT_PRE":
            close_time = now.replace(hour=8, minute=50, second=0, microsecond=0)
        elif session == "NXT_AFTER":
            close_time = now.replace(hour=20, minute=0, second=0, microsecond=0)
        else:
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

        context = (
            f"현재 시각: {now.strftime('%H:%M')} | "
            f"장 마감까지: {minutes_left}분 (AI가 종목별 보유/청산 판단)\n"
            f"오늘 누적 손익: {daily_pnl_pct:+.2f}% | "
            f"매매 성적: {stats['wins']}승 {stats['losses']}패 "
            f"(총 {stats['total']}건)"
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

    async def _get_today_trade_count(self) -> int:
        """당일 신규 진입(매수 체결) 건수 — trade_results 기준"""
        try:
            from models.trade_result import TradeResult
            from sqlalchemy import select, func
            from util.time_util import now_kst

            today = now_kst().date()
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(func.count(TradeResult.id)).where(
                        TradeResult.side == "BUY",
                        TradeResult.status == "CONFIRMED",
                        func.date(TradeResult.entry_at) == today,
                    )
                )
                return result.scalar() or 0
        except Exception as e:
            logger.warning("당일 체결 건수 조회 실패: {}", str(e))
            return 0

    async def _ensure_realtime_subscription(self, symbol: str) -> None:
        """매수 후 WebSocket 실시간 구독 확인/추가"""
        try:
            from realtime.stream_manager import stream_manager
            await stream_manager.subscribe_symbols([(symbol, "KRX")])
            logger.debug("매수 종목 WebSocket 구독 추가: {}", symbol)
        except Exception as e:
            logger.warning("WebSocket 구독 추가 실패 ({}): {}", symbol, str(e))

    def _parse_json(self, text: str) -> dict | None:
        from core.json_utils import parse_llm_json
        result = parse_llm_json(text)
        return result if result else None

    @property
    def last_cycle_time(self):
        return self._last_cycle_time


trading_agent = TradingAgent()
