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

    # 정점 판단 매도(ANALYSIS_SELL) 게이트 — 검증 데이터상 조기매도 편향(매도 후 더 비싸게 재매수)
    # 적중률이 입증되기 전에는 수익 중인 종목만 정점 매도 허용 (러너 조기 청산 차단)
    PEAK_SELL_MIN_PROFIT_PCT = 2.0   # 미입증 상태에서 정점 매도 허용 최소 수익률
    PEAK_SELL_TRUST_RATE = 50.0      # 적중률 이 이상이면 게이트 해제
    PEAK_SELL_TRUST_SAMPLES = 10     # 적중률 판단 최소 표본

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
        """시장가 매도 실행 — 종목별 lock으로 매수/매도/실시간 손절 race 방지"""
        if not settings.TRADING_ENABLED:
            return False

        # 종목별 lock — buy_agent.execute, event_detector 손절과 동일 lock
        from agent.trading_agent import trading_agent
        async with trading_agent.get_symbol_lock(params.symbol):
            return await self._execute_sell_locked(params)

    async def _execute_sell_locked(self, params: SellParams) -> bool:
        try:
            from trading.account_manager import account_manager
            holdings = await account_manager.get_holdings()
            holding = next((h for h in holdings if h.symbol == params.symbol), None)
            if not holding or holding.quantity <= 0:
                return False

            # 권한 가중 게이트: LLM 정점 판단 매도는 적중률 입증 전까지 수익 게이트 통과 필요
            # (차단돼도 손절/트레일링 등 코드 로직이 하방 방어)
            if params.exit_reason == "ANALYSIS_SELL":
                if not await self._peak_sell_allowed(holding):
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
                    exit_reason=params.exit_reason,
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

                # 매도 성공 → 재진입 cooldown 등록 (손절·익절 통합)
                try:
                    from agent.trading_agent import trading_agent
                    trading_agent.set_exit_cooldown(params.symbol)
                except Exception as e:
                    logger.debug("[SellAgent] cooldown 등록 실패 ({}): {}", params.symbol, str(e))

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

    async def _peak_sell_allowed(self, holding) -> bool:
        """정점 판단 매도 게이트 — 적중률 입증 시 무조건 허용, 아니면 수익 +2% 이상만"""
        pnl_rate = holding.pnl_rate or 0.0

        proven = False
        try:
            from analysis.feedback.judgment_verifier import judgment_verifier
            stats = await judgment_verifier.get_accuracy_stats()
            s = stats.get("PEAK_SELL")
            proven = bool(
                s and s["n"] >= self.PEAK_SELL_TRUST_SAMPLES
                and s["rate"] >= self.PEAK_SELL_TRUST_RATE
            )
        except Exception as e:
            logger.debug("[SellAgent] 정점 판단 적중률 조회 실패: {}", str(e))

        if proven or pnl_rate >= self.PEAK_SELL_MIN_PROFIT_PCT:
            return True

        logger.info(
            "[SellAgent] 정점 판단 매도 보류: {} 수익률 {:+.2f}% < +{:.1f}% "
            "(적중률 미입증 — 손절/트레일링이 하방 방어)",
            holding.symbol, pnl_rate, self.PEAK_SELL_MIN_PROFIT_PCT,
        )
        await activity_logger.log(
            ActivityType.ORDER, ActivityPhase.PROGRESS,
            f"⏸️ 정점 판단 매도 보류: {holding.symbol} 수익률 {pnl_rate:+.2f}% "
            f"< +{self.PEAK_SELL_MIN_PROFIT_PCT:.1f}% 게이트 (조기매도 편향 방지)",
            symbol=holding.symbol,
        )
        return False


# 싱글톤
sell_agent = SellAgent()
