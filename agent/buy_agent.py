"""BuyAgent — 매수 실행 전담

분석은 StockAnalysisAgent가 담당. BuyAgent는 실행에 필요한 값만 받아서 매수만 실행.
"""
from dataclasses import dataclass

from loguru import logger

from agent.base import BaseAgent
from agent.decision_maker import decision_maker
from core.config import settings
from realtime.event_detector import event_detector
from services.activity_logger import activity_logger
from strategy.risk_manager import risk_manager
from trading.enums import ActivityPhase, ActivityType, SignalAction, SignalUrgency


@dataclass
class BuyParams:
    """매수 실행에 필요한 값만"""
    symbol: str
    name: str
    strategy_type: str
    price: float
    confidence: float
    reason: str
    # 포지션 비중 제한 (AI Risk Tuner가 결정, 0이면 기본 20%)
    max_position_pct: float = 0.0
    # PriceGuard 설정용 수치
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_pct: float = 0.0
    breakeven_trigger_pct: float = 0.0
    review_threshold_pct: float = 0.0
    review_interval_min: int = 0  # 다음 재평가까지 분 (LLM이 분석 결과 기반 결정)


class BuyAgent(BaseAgent):
    """매수 실행 전담 — 분석 없음, 실행만"""

    @property
    def name(self) -> str:
        return "BuyAgent"

    def __init__(self):
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("BuyAgent 시작")

    async def stop(self) -> None:
        self._running = False

    async def execute(self, params: BuyParams) -> dict:
        """매수 실행 — 리스크 검증 → 주문 → PriceGuard 등록

        종목별 lock으로 동시 매수/매도/실시간 손절 race 방지.
        """
        result = {"symbol": params.symbol, "executed": False}

        # 종목별 lock — 사이클 매수 vs 실시간 손절 race 방지
        from agent.trading_agent import trading_agent
        async with trading_agent.get_symbol_lock(params.symbol):
            return await self._execute_locked(params, result)

    async def _execute_locked(self, params: BuyParams, result: dict) -> dict:
        try:
            from trading.account_manager import account_manager
            balance, holdings = await account_manager.get_account_snapshot()

            if params.price <= 0:
                return result

            # 최소 신뢰도 게이트 — TradingRule(min_confidence)의 유일한 실집행 지점
            # (규칙 엔진이 전략 인스턴스 속성에 오버라이드 → 여기서 읽음.
            #  구 Strategy.evaluate() 게이트는 라이브 미호출로 제거됨)
            from agent.trading_agent import trading_agent
            strategy = trading_agent.strategies.get(params.strategy_type)
            min_conf = getattr(strategy, "min_confidence", 0.5) if strategy else 0.5
            if 0 < params.confidence < min_conf:
                logger.info("[BuyAgent] 신뢰도 미달 — 주문 스킵: {} {:.2f} < {:.2f}",
                            params.symbol, params.confidence, min_conf)
                await activity_logger.log(
                    ActivityType.DECISION, ActivityPhase.SKIP,
                    f"⚠️ [{params.symbol}] 최소 신뢰도 미달 → 매수 스킵 "
                    f"({params.confidence:.2f} < {min_conf:.2f})",
                    symbol=params.symbol,
                )
                return result

            # 주문 직전 현재가 재조회 — 분석 레이턴시 동안 시세 변동 보정
            # (SellAgent의 NXT 현재가 재조회 패턴과 동일 컨셉, BuyAgent 버전)
            analysis_price = params.price
            fresh_price = params.price
            try:
                from trading.mcp_client import mcp_client as _mcp
                price_resp = await _mcp.get_current_price(params.symbol)
                if price_resp.success and price_resp.data:
                    fp = float(
                        price_resp.data.get("price")
                        or price_resp.data.get("current_price")
                        or 0
                    )
                    if fp > 0:
                        fresh_price = fp
            except Exception as e:
                logger.warning("[BuyAgent] 현재가 재조회 실패 ({}): {} — 분석가 유지",
                               params.symbol, str(e))

            # 드리프트 계산 — AI 분석 시점 대비 변동률
            drift_pct = (
                abs(fresh_price - analysis_price) / analysis_price * 100
                if analysis_price > 0 else 0.0
            )
            # AI의 target/stop 경계 이탈 체크
            # - fresh_price >= target_price: 이미 목표 도달 → 상승여력 없음
            # - fresh_price <= stop_loss_price: 리스크 현실화 → 진입 근거 상실
            invalid_by_bounds = (
                (params.take_profit_price > 0 and fresh_price >= params.take_profit_price)
                or (params.stop_loss_price > 0 and fresh_price <= params.stop_loss_price)
            )

            if drift_pct > settings.ORDER_PRICE_DRIFT_MAX_PCT or invalid_by_bounds:
                reason = (
                    f"변동 {drift_pct:+.1f}% > 허용 {settings.ORDER_PRICE_DRIFT_MAX_PCT}%"
                    if drift_pct > settings.ORDER_PRICE_DRIFT_MAX_PCT
                    else f"AI 경계 이탈 (target {params.take_profit_price:,.0f}/stop {params.stop_loss_price:,.0f})"
                )
                logger.info("[BuyAgent] AI 분석 무효 — 주문 스킵: {} {:,.0f}→{:,.0f} ({})",
                            params.symbol, analysis_price, fresh_price, reason)
                await activity_logger.log(
                    ActivityType.DECISION, ActivityPhase.SKIP,
                    f"⚠️ [{params.symbol}] 가격 변동으로 AI 분석 무효 → 주문 스킵: "
                    f"{analysis_price:,.0f}→{fresh_price:,.0f}원 ({reason})",
                    symbol=params.symbol,
                )
                return result  # executed=False

            # 드리프트 허용 범위 내 → 최신 가격으로 주문가 확정 (호가 단위 정규화 필수)
            from util.kis_price import round_to_tick
            if fresh_price != analysis_price:
                diff_pct = (fresh_price - analysis_price) / analysis_price * 100
                logger.info("[BuyAgent] 주문가 확정: {} {:,.0f}→{:,.0f} ({:+.2f}%)",
                            params.symbol, analysis_price, fresh_price, diff_pct)
                params.price = fresh_price  # 이후 수량 계산도 최신 가격 기준
            # KIS 호가 단위 정규화 (rt_cd=7 "주식주문호가단위 오류" 방지)
            normalized = round_to_tick(params.price, mode="round")
            if normalized != int(params.price):
                logger.info("[BuyAgent] 호가 단위 정규화: {} {:,.0f}→{:,}원",
                            params.symbol, params.price, normalized)
                params.price = float(normalized)

            # 실제 주문가능금액 조회 (미체결 증거금 차감된 진짜 가용 현금)
            from trading.kis_api import get_buying_power
            bp = await get_buying_power(params.symbol, price=int(params.price))
            buying_cash = bp.get("available_cash", 0) if bp.get("success") else balance.cash
            max_qty_by_cash = bp.get("max_qty", 0) if bp.get("success") else 0

            if buying_cash < params.price:
                logger.info("[BuyAgent] 주문가능금액 부족: {} {:,.0f}원 < {:,.0f}원 (1주)",
                            params.symbol, buying_cash, params.price)
                return result

            # 매수 수량 계산 — 동적 한도 + 실제 주문가능금액 기준
            from agent.trading_agent import trading_agent
            dyn = getattr(trading_agent, '_dynamic_limits', None) or {}
            max_pos_pct = params.max_position_pct if params.max_position_pct > 0 else dyn.get("max_position_pct", 100.0)
            max_invest = min(buying_cash, balance.total_asset * max_pos_pct / 100)
            suggested_qty = max(1, int(max_invest / params.price))
            # KIS가 알려준 최대 수량으로 상한 제한
            if max_qty_by_cash > 0:
                suggested_qty = min(suggested_qty, max_qty_by_cash)
            if suggested_qty <= 0:
                return result

            # TradeSignal 생성
            from strategy.signal import TradeSignal
            signal = TradeSignal(
                symbol=params.symbol,
                stock_id="",
                action=SignalAction.BUY,
                strength=params.confidence,
                suggested_price=params.price,
                suggested_quantity=suggested_qty,
                target_price=params.take_profit_price,
                stop_loss_price=params.stop_loss_price,
                urgency=SignalUrgency.WAIT,
                strategy_type=params.strategy_type,
                reason=params.reason,
                confidence=params.confidence,
            )

            # 리스크 검증
            today_trade_count = 0
            try:
                from agent.trading_agent import trading_agent
                today_trade_count = await trading_agent._get_today_trade_count()
            except Exception:
                pass

            risk_result = await risk_manager.check(
                signal=signal,
                portfolio_cash=balance.cash,
                portfolio_budget=balance.total_asset,
                today_trade_count=today_trade_count,
                current_holding_count=len(holdings),
                dynamic_limits=dyn,
                market_regime=getattr(trading_agent, '_market_regime', ''),
            )
            if not risk_result.get("approved"):
                logger.info("[BuyAgent] 리스크 거부 ({}): {}", params.symbol, risk_result.get("reason"))
                return result

            adjusted_qty = risk_result.get("adjusted_quantity")
            if adjusted_qty:
                signal.suggested_quantity = adjusted_qty

            # 주문 실행
            if not settings.TRADING_ENABLED:
                return result

            exec_result = await decision_maker.execute(
                signal=signal,
                analysis_context={
                    "stock_name": params.name,
                    "strategy_type": params.strategy_type,
                    "ai_recommendation": "BUY",
                    "ai_confidence": params.confidence,
                    "ai_target_price": params.take_profit_price,
                    "ai_stop_loss_price": params.stop_loss_price,
                },
            )

            if exec_result.get("success"):
                # 러너 종목 추가 매수 → 러너 해제 (신규 목표가로 정상 관리 재개)
                if event_detector.get_thresholds(params.symbol).is_runner:
                    await self._clear_runner(params.symbol)

                # PriceGuard에 LLM 수치 설정
                kwargs = {}
                if params.stop_loss_price > 0:
                    kwargs["stop_loss"] = params.stop_loss_price
                    kwargs["initial_stop_loss"] = params.stop_loss_price
                if params.take_profit_price > 0:
                    kwargs["take_profit"] = params.take_profit_price
                    kwargs["initial_take_profit"] = params.take_profit_price
                if params.trailing_stop_pct > 0:
                    kwargs["trailing_stop_pct"] = params.trailing_stop_pct
                if params.breakeven_trigger_pct > 0:
                    kwargs["breakeven_trigger_pct"] = params.breakeven_trigger_pct
                if params.review_threshold_pct > 0:
                    kwargs["review_threshold_pct"] = params.review_threshold_pct
                if params.review_interval_min > 0:
                    kwargs["review_interval_min"] = params.review_interval_min
                if params.price > 0:
                    kwargs["entry_price"] = params.price
                if kwargs:
                    event_detector.set_thresholds(params.symbol, **kwargs)

                await activity_logger.log(
                    ActivityType.ORDER, ActivityPhase.COMPLETE,
                    f"✅ 매수 주문 접수 (체결 대기중): {params.name}({params.symbol})",
                    symbol=params.symbol,
                )

                # WebSocket 구독
                try:
                    from realtime.stream_manager import stream_manager
                    from scheduler.market_calendar import market_calendar
                    await stream_manager.subscribe_symbols([(params.symbol, market_calendar.get_active_market())])
                except Exception:
                    pass

                result["executed"] = True

        except Exception as e:
            logger.error("[BuyAgent] 매수 실행 오류 ({}): {}", params.symbol, str(e))

        return result

    async def _clear_runner(self, symbol: str) -> None:
        """러너 해제 — 메모리 플래그 + DB open BUY 행 동기화 (재시작 복원 일관성)"""
        event_detector.set_thresholds(symbol, is_runner=False)
        try:
            from core.database import AsyncSessionLocal
            from repositories.trade_result_repository import TradeResultRepository

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    for tr in await repo.get_all_open_buys(symbol):
                        tr.is_runner = False
            logger.info("러너 해제: {} (추가 매수 — 정상 관리 재개)", symbol)
        except Exception as e:
            logger.warning("[BuyAgent] 러너 해제 DB 동기화 실패 ({}): {}", symbol, str(e))


# 싱글톤
buy_agent = BuyAgent()
