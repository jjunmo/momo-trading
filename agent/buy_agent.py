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
        """매수 실행 — 리스크 검증 → 주문 → PriceGuard 등록"""
        result = {"symbol": params.symbol, "executed": False}

        try:
            from trading.account_manager import account_manager
            balance, holdings = await account_manager.get_account_snapshot()

            if params.price <= 0:
                return result

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
                    f"✅ 매수 실행: {params.name}({params.symbol})",
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


# 싱글톤
buy_agent = BuyAgent()
