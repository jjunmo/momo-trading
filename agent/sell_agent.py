"""SellAgent — 매도 실행 + 임계값 업데이트 전담

분석은 StockAnalysisAgent가 담당. SellAgent는 실행만.
- SELL → 시장가 매도
- HOLD → PriceGuard 임계값 업데이트
- BUY → BuyAgent에 매수 위임
"""
from dataclasses import dataclass

from loguru import logger

from agent.base import BaseAgent
from agent.decision_maker import decision_maker
from core.config import settings
from realtime.event_detector import event_detector
from services.activity_logger import activity_logger
from trading.enums import ActivityPhase, ActivityType
from trading.mcp_client import mcp_client


@dataclass
class SellParams:
    """매도 실행에 필요한 값만"""
    symbol: str
    exit_reason: str = "SIGNAL"


class SellAgent(BaseAgent):
    """매도 실행 전담 — 분석 없음, 실행만"""

    @property
    def name(self) -> str:
        return "SellAgent"

    def __init__(self):
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("SellAgent 시작")

    async def stop(self) -> None:
        self._running = False

    async def execute_sell(self, params: SellParams) -> bool:
        """시장가 매도 실행"""
        if not settings.TRADING_ENABLED:
            return False

        try:
            from trading.account_manager import account_manager
            holdings = await account_manager.get_holdings()
            holding = next((h for h in holdings if h.symbol == params.symbol), None)
            if not holding or holding.quantity <= 0:
                return False

            saved = event_detector.get_thresholds(params.symbol)
            event_detector.remove_levels(params.symbol)

            from scheduler.market_calendar import market_calendar
            excg_cd = market_calendar.get_excg_dvsn_cd()
            # NXT/SOR 지정가 주문: 현재가를 주문가로 사용
            sell_price = holding.current_price if excg_cd in ("NXT", "SOR") else None
            resp = await mcp_client.place_order(
                symbol=params.symbol, side="SELL",
                quantity=holding.quantity, price=sell_price,
                market=excg_cd,
            )

            await activity_logger.log(
                ActivityType.ORDER, ActivityPhase.COMPLETE,
                f"{'✅' if resp.success else '❌'} 매도: {params.symbol} {holding.quantity}주 "
                f"[{params.exit_reason}]",
                symbol=params.symbol,
            )

            if resp.success:
                order_data = resp.data or {}
                order_id = order_data.get("order_id", "")
                expected_price = holding.current_price or 0

                # DB에 PENDING_CONFIRM 레코드 생성 (서버 재시작 시 복구용)
                pending_id = await decision_maker._create_pending_record(
                    symbol=params.symbol, side="SELL",
                    order_id=order_id, quantity=holding.quantity,
                    expected_price=expected_price,
                )

                # 보유종목 변동 감지로 체결 확인 (백그라운드)
                import asyncio
                asyncio.create_task(
                    decision_maker.wait_for_sell_confirmation(
                        symbol=params.symbol, order_id=order_id,
                        quantity=holding.quantity,
                        expected_price=expected_price,
                        exit_reason=params.exit_reason,
                    )
                )
                # NOTE: 이전에 여기서 `market_scanner.add_untradeable(params.symbol)`를
                # 무조건 호출했음 → 매도한 종목이 영구 블록되어 재매수 불가 (버그).
                # 매매불가 블록은 decision_maker에서 실제 에러 메시지 기반으로만 수행.
                return True
            else:
                # 실패 → 임계값 복원
                kwargs = {}
                if saved.stop_loss > 0:
                    kwargs["stop_loss"] = saved.stop_loss
                if saved.take_profit > 0:
                    kwargs["take_profit"] = saved.take_profit
                if saved.trailing_stop_pct > 0:
                    kwargs["trailing_stop_pct"] = saved.trailing_stop_pct
                if kwargs:
                    event_detector.set_thresholds(params.symbol, **kwargs)
                return False
        except Exception as e:
            logger.error("[SellAgent] 매도 실행 실패 ({}): {}", params.symbol, str(e))
            return False



# 싱글톤
sell_agent = SellAgent()
