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
from trading.enums import LLM_SELL_REASONS, ActivityPhase, ActivityType
from trading.mcp_client import mcp_client


@dataclass
class SellParams:
    """매도 실행에 필요한 값만"""
    symbol: str
    exit_reason: str = "SIGNAL"
    quantity: int = 0  # 0 = 전량 매도


class SellAgent(BaseAgent):
    """매도 실행 전담 — 분석 없음, 실행만"""

    # LLM 판단 매도 3경로(ANALYSIS_SELL·HOLDINGS_REVIEW·TAKE_PROFIT_REVIEW) 공통 게이트
    # — 검증 데이터상 조기매도 편향(매도 후 더 비싸게 재매수)
    # 적중률이 입증되기 전에는 수익 중인 종목만 LLM 판단 매도 허용 (러너 조기 청산 차단)
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

            saved = event_detector.get_thresholds(params.symbol)

            # 러너 가드: 러너 잔량은 기계적 트레일링 전용 — LLM 판단 매도 무시
            # (STOP_LOSS/TRAILING_STOP/DAY_CLOSE 등은 집합 밖이라 정상 통과)
            if saved.is_runner and params.exit_reason in LLM_SELL_REASONS:
                logger.info("[SellAgent] 러너 종목 LLM 매도 무시: {} (트레일링 전용)", params.symbol)
                return False

            # 권한 가중 게이트: LLM 판단 매도(3경로)는 적중률 입증 전까지 수익 게이트 통과 필요
            # (차단돼도 손절/트레일링 등 코드 로직이 하방 방어)
            if params.exit_reason in LLM_SELL_REASONS:
                if not await self._peak_sell_allowed(holding):
                    return False

            # 분할 익절 판단 — LLM 판단 매도 + 수익 중이면 절반만 실현, 잔량은 러너
            # (손실 중 LLM SELL은 전량: 러너 본전스탑이 현재가 위라 즉시 재트리거되는 낭비 방지)
            sell_qty, to_runner = holding.quantity, False
            if params.quantity > 0:
                sell_qty = min(params.quantity, holding.quantity)
            elif params.exit_reason in LLM_SELL_REASONS and (holding.pnl_rate or 0) > 0:
                half = int(holding.quantity * settings.PARTIAL_TP_RATIO)
                if half >= 1 and holding.quantity - half >= 1:
                    sell_qty, to_runner = half, True  # 1주 보유 등 분할 불가 → 전량 폴백

            if not to_runner:
                event_detector.remove_levels(params.symbol)

            from scheduler.market_calendar import market_calendar
            excg_cd = market_calendar.get_excg_dvsn_cd()
            # NXT/SOR 지정가 주문: 현재가를 주문가로 사용
            sell_price = holding.current_price if excg_cd in ("NXT", "SOR") else None
            resp = await mcp_client.place_order(
                symbol=params.symbol, side="SELL",
                quantity=sell_qty, price=sell_price,
                market=excg_cd,
            )

            await activity_logger.log(
                ActivityType.ORDER, ActivityPhase.COMPLETE,
                f"{'✅' if resp.success else '❌'} 매도: {params.symbol} "
                f"{sell_qty}/{holding.quantity}주 [{params.exit_reason}]"
                f"{' → 잔량 러너 전환' if to_runner else ''}",
                symbol=params.symbol,
            )

            if resp.success:
                order_data = resp.data or {}
                order_id = order_data.get("order_id", "")
                expected_price = holding.current_price or 0

                # DB에 PENDING_CONFIRM 레코드 생성 (서버 재시작 시 복구용)
                pending_id = await decision_maker._create_pending_record(
                    symbol=params.symbol, side="SELL",
                    order_id=order_id, quantity=sell_qty,
                    expected_price=expected_price,
                    exit_reason=params.exit_reason,
                )

                # 잔량 러너 전환 — take_profit 해제, 본전 이상 스탑, 트레일링 전용
                if to_runner:
                    await self._convert_to_runner(params.symbol, holding, saved)

                # 보유종목 변동 감지로 체결 확인 (백그라운드)
                import asyncio
                asyncio.create_task(
                    decision_maker.wait_for_sell_confirmation(
                        symbol=params.symbol, order_id=order_id,
                        quantity=sell_qty,
                        expected_price=expected_price,
                        exit_reason=params.exit_reason,
                        held_qty=holding.quantity,
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
                # 실패 → 임계값 복원 (러너 경로는 remove_levels 안 했으므로 복원 불요)
                if not to_runner:
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

    async def _convert_to_runner(self, symbol: str, holding, th) -> None:
        """잔량 러너 전환 — take_profit 해제, 본전 이상 스탑, 트레일링 전용 청산

        메모리(event_detector) + DB(open BUY 행) 동시 갱신.
        ai_target_price는 보존 (BUY_ANALYSIS 채점용 — 복원 차단은 is_runner가 담당).
        """
        entry = th.entry_price or holding.avg_buy_price or 0
        trailing = th.trailing_stop_pct or settings.RUNNER_TRAILING_FALLBACK_PCT
        # 본전 이상 상향 — 진짜 본전은 매입가 + 왕복 수수료·거래세 (~0.2%)
        from util.pnl_calculator import breakeven_price
        new_stop = max(th.stop_loss, breakeven_price(entry))
        event_detector.set_thresholds(
            symbol,
            is_runner=True, take_profit=0.0,
            stop_loss=new_stop, trailing_stop_pct=trailing,
            highest_price=max(th.highest_price, holding.current_price or 0),
        )

        # DB 영속화: open BUY 전 행 is_runner=True + 스탑 갱신 (재시작 복원용)
        try:
            from core.database import AsyncSessionLocal
            from repositories.trade_result_repository import TradeResultRepository

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    for tr in await repo.get_all_open_buys(symbol):
                        tr.is_runner = True
                        if new_stop > 0:
                            tr.ai_stop_loss_price = new_stop
        except Exception as e:
            logger.warning("[SellAgent] 러너 DB 영속화 실패 ({}): {}", symbol, str(e))

        logger.info("러너 전환: {} 본전스탑 {:,.0f}원, 트레일링 {:.1f}%", symbol, new_stop, trailing)
        await activity_logger.log(
            ActivityType.ORDER, ActivityPhase.PROGRESS,
            f"🏃 러너 전환: {symbol} 본전스탑 {new_stop:,.0f}원, "
            f"트레일링 {trailing:.1f}% (익절 재검토 중단, 트레일링 전용)",
            symbol=symbol,
        )

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
