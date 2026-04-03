"""매매 결정 + 자율/반자율 모드 분기 + 체결 확인/기록"""
import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from loguru import logger

from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, EventType, event_bus
from models.order import Order
from models.recommendation import Recommendation
from models.trade_result import TradeResult
from repositories.trade_result_repository import TradeResultRepository
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import ActivityPhase, ActivityType, AutonomyMode, OrderConfirmStatus, OrderSource, RecommendationStatus
from trading.mcp_client import mcp_client
from util.time_util import now_kst


class DecisionMaker:
    """
    자율/반자율 모드에 따라 실행 방식을 분기.

    AUTONOMOUS: 스캔 → 분석 → 매매까지 전자동
    SEMI_AUTO: 스캔 → 분석 → 추천 생성 → 사용자 승인 대기
    """

    def __init__(self):
        self._pending_tasks: set[asyncio.Task] = set()

    async def await_pending_tasks(self, timeout: float = 30.0) -> int:
        """대기 중인 체결 확인 태스크를 모두 완료할 때까지 대기.

        Returns: 완료된 태스크 수
        """
        if not self._pending_tasks:
            return 0
        pending = list(self._pending_tasks)
        logger.info("체결 확인 태스크 {}건 완료 대기 (timeout={}s)", len(pending), timeout)
        done, not_done = await asyncio.wait(pending, timeout=timeout)
        if not_done:
            logger.warning("체결 확인 태스크 {}건 타임아웃 미완료", len(not_done))
        return len(done)

    async def execute(
        self, signal: TradeSignal, analysis_id: str = "", cycle_id: str | None = None,
        analysis_context: dict | None = None,
        on_settled: "Callable[[str, bool], Any] | None" = None,
    ) -> dict:
        """시그널에 따라 실행"""
        mode = AutonomyMode(settings.AUTONOMY_MODE)

        if mode == AutonomyMode.AUTONOMOUS:
            return await self._execute_autonomous(signal, cycle_id, analysis_context, on_settled=on_settled)
        else:
            return await self._create_recommendation(signal, analysis_id, cycle_id)

    async def _execute_autonomous(
        self, signal: TradeSignal, cycle_id: str | None = None,
        analysis_context: dict | None = None,
        on_settled: Callable[[str, bool], Any] | None = None,
    ) -> dict:
        """완전자율: MCP로 즉시 주문 실행"""
        logger.info(
            "[AUTONOMOUS] 주문 실행: {} {} x{} {}",
            signal.symbol, signal.action.value,
            signal.suggested_quantity,
            f"@{signal.suggested_price}" if signal.suggested_price else "시장가",
        )

        qty = signal.suggested_quantity or 0
        price = signal.suggested_price or 0
        amount = price * qty
        price_display = f"@{price:,.0f}원" if price else "시장가"
        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.START,
            f"\U0001f4b0 [{signal.symbol}] 자동 주문 실행: "
            f"{signal.action.value} {qty}주 "
            f"{price_display}" + (f" ({amount:,.0f}원)" if amount else ""),
            cycle_id=cycle_id,
            symbol=signal.symbol,
        )

        response = await mcp_client.place_order(
            symbol=signal.symbol,
            side=signal.action.value,
            quantity=signal.suggested_quantity or 0,
            price=signal.suggested_price,
        )

        # 주문 응답 검증: MCP success + 주문번호 존재 확인
        # mcp_client.place_order()가 이미 order_id를 정규화함
        order_data = response.data or {}
        order_id = order_data.get("order_id", "")
        is_submitted = response.success and bool(order_id)

        result = {
            "mode": "AUTONOMOUS",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "success": is_submitted,
            "order_id": order_id,
            "message": "주문 접수" if is_submitted else (response.error or "주문 응답 없음"),
            "data": response.data,
        }

        if is_submitted:
            await activity_logger.log(
                ActivityType.DECISION, ActivityPhase.COMPLETE,
                f"\u2705 [{signal.symbol}] 주문 접수 완료 (체결 대기) — 주문번호: {order_id}",
                cycle_id=cycle_id, symbol=signal.symbol,
                detail=result,
            )
            # PENDING_CONFIRM 레코드 즉시 생성 (체결 확인 실패해도 DB에 기록 남음)
            pending_record_id = await self._create_pending_record(
                symbol=signal.symbol,
                side=signal.action.value,
                order_id=order_id,
                quantity=qty,
                expected_price=price,
                analysis_context=analysis_context,
            )
            # 체결 확인 + TradeResult 업데이트 (백그라운드, 매매 흐름 차단 안 함)
            task = asyncio.create_task(
                self.confirm_and_record(
                    symbol=signal.symbol,
                    side=signal.action.value,
                    order_id=order_id,
                    quantity=qty,
                    expected_price=price,
                    analysis_context=analysis_context,
                    cycle_id=cycle_id,
                    pending_record_id=pending_record_id,
                    on_settled=on_settled,
                )
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        else:
            error_msg = response.error or "주문번호 없음"
            # 매매불가 종목 → 런타임 블록리스트 등록 (이후 스캔에서 제외)
            if "매매불가" in error_msg:
                from agent.market_scanner import market_scanner
                market_scanner.add_untradeable(signal.symbol)
                logger.warning("매매불가 종목 블록리스트 등록: {} → 이후 스캔에서 제외", signal.symbol)
            await activity_logger.log(
                ActivityType.DECISION, ActivityPhase.ERROR,
                f"\u274c [{signal.symbol}] 주문 실패: {error_msg}",
                cycle_id=cycle_id, symbol=signal.symbol,
                error_message=error_msg,
            )

        await event_bus.publish(Event(
            type=EventType.ORDER_EXECUTED,
            data=result,
            source="decision_maker",
        ))

        return result

    async def _create_pending_record(
        self,
        symbol: str,
        side: str,
        order_id: str,
        quantity: int,
        expected_price: float,
        analysis_context: dict | None = None,
    ) -> str | None:
        """PENDING_CONFIRM 상태의 TradeResult를 즉시 DB에 저장 (고아 주문 방지)"""
        ctx = analysis_context or {}
        now = now_kst()
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    # 중복 체크 (P2-7)
                    existing = await repo.get_by_order_id(order_id)
                    if existing:
                        logger.warning("[{}] 주문번호 {} 이미 존재 → pending 생성 스킵", symbol, order_id)
                        return existing.id
                    tr = TradeResult(
                        order_id=order_id,
                        stock_symbol=symbol,
                        stock_name=ctx.get("stock_name", symbol),
                        side=side,
                        strategy_type=ctx.get("strategy_type", ""),
                        entry_price=expected_price if side == "BUY" else 0.0,
                        exit_price=expected_price if side == "SELL" else 0.0,
                        quantity=quantity,
                        status=OrderConfirmStatus.PENDING_CONFIRM.value,
                        ai_recommendation=ctx.get("ai_recommendation", ""),
                        ai_confidence=ctx.get("ai_confidence", 0.0),
                        ai_target_price=ctx.get("ai_target_price"),
                        ai_stop_loss_price=ctx.get("ai_stop_loss_price"),
                        market_regime=ctx.get("market_regime", ""),
                        entry_at=now if side == "BUY" else None,
                        exit_at=now if side == "SELL" else None,
                        notes="PENDING_CONFIRM: 체결 확인 대기 중",
                    )
                    session.add(tr)
                    await session.flush()
                    logger.debug("[{}] PENDING_CONFIRM 레코드 생성: order_id={}", symbol, order_id)
                    return tr.id
        except Exception as e:
            logger.error("[{}] PENDING_CONFIRM 레코드 생성 실패: {}", symbol, str(e))
            return None

    async def confirm_and_record(
        self,
        symbol: str,
        side: str,
        order_id: str,
        quantity: int,
        expected_price: float,
        analysis_context: dict | None = None,
        cycle_id: str | None = None,
        exit_reason: str = "",
        pending_record_id: str | None = None,
        on_settled: Callable[[str, bool], Any] | None = None,
    ) -> None:
        """주문 접수 후 체결 확인 → TradeResult 기록

        pending_record_id가 있으면 기존 PENDING_CONFIRM 레코드를 UPDATE.
        없으면 기존 방식(새 레코드 생성)으로 폴백.
        3초 대기 → get_order_list()로 체결 확인 → 체결 시 기록.
        on_settled: 체결 확인 완료 시 호출되는 콜백 (order_id, success)
        """
        try:
            await asyncio.sleep(3)  # KIS 체결 처리 대기

            resp = await mcp_client.get_order_list()
            if not resp.success:
                logger.warning("[{}] 주문내역 조회 실패: {}", symbol, resp.error)
                await self._mark_pending_failed(pending_record_id, f"주문내역 조회 실패: {resp.error}")
                if on_settled:
                    await on_settled(order_id, False)
                return

            # 응답 구조 로깅 (첫 호출 디버깅용)
            logger.debug("[체결확인] get_order_list 응답: {}", str(resp.data)[:500])

            # KIS 주문내역 응답 파싱: output 또는 output1 배열
            orders = []
            if isinstance(resp.data, dict):
                orders = (
                    resp.data.get("output", [])
                    or resp.data.get("output1", [])
                    or resp.data.get("orders", [])
                )
                if isinstance(orders, dict):
                    orders = [orders]
            elif isinstance(resp.data, list):
                orders = resp.data

            # order_id 매칭으로 체결 확인
            filled_order = None
            for order in orders:
                if not isinstance(order, dict):
                    continue
                # KIS 주문번호 키: odno (대소문자 혼용)
                kis_odno = (
                    order.get("odno") or order.get("ODNO")
                    or order.get("order_id") or ""
                )
                if str(kis_odno) == str(order_id):
                    filled_order = order
                    break

            if not filled_order:
                logger.debug("[{}] 주문 {} 미체결 (체결내역에서 미발견) → 취소 시도", symbol, order_id)
                await self._cancel_unfilled_order(order_id, symbol)
                if on_settled:
                    await on_settled(order_id, False)
                return

            # 체결 수량/가격 추출
            filled_qty = mcp_client._to_int(
                filled_order.get("tot_ccld_qty")
                or filled_order.get("filled_quantity")
                or filled_order.get("ccld_qty")
                or quantity
            )
            filled_price = mcp_client._to_float(
                filled_order.get("avg_prvs")
                or filled_order.get("ccld_pric")
                or filled_order.get("filled_price")
                or expected_price
            )

            if filled_qty <= 0:
                logger.debug("[{}] 주문 {} 체결수량 0 → 미체결 → 취소 시도", symbol, order_id)
                await self._cancel_unfilled_order(order_id, symbol)
                if on_settled:
                    await on_settled(order_id, False)
                return

            logger.info(
                "[체결확인] {} {} {}주 @{:,.0f}원 체결 완료 (주문번호: {})",
                symbol, side, filled_qty, filled_price, order_id,
            )

            # pending 레코드가 있으면 UPDATE, 없으면 CREATE
            if pending_record_id:
                await self._confirm_pending_record(
                    pending_record_id=pending_record_id,
                    symbol=symbol,
                    side=side,
                    filled_qty=filled_qty,
                    filled_price=filled_price,
                    analysis_context=analysis_context,
                    exit_reason=exit_reason,
                    cycle_id=cycle_id,
                )
            else:
                await self._record_trade_result(
                    symbol=symbol,
                    side=side,
                    order_id=order_id,
                    filled_qty=filled_qty,
                    filled_price=filled_price,
                    analysis_context=analysis_context,
                    exit_reason=exit_reason,
                    cycle_id=cycle_id,
                )

            # 체결 확인 후 계좌 캐시 무효화 → 다음 조회 시 최신 반영
            from trading.account_manager import account_manager
            account_manager.invalidate_cache()

            # 체결 성공 콜백 → 예약 금액 해제
            if on_settled:
                await on_settled(order_id, True)

        except Exception as e:
            logger.error("[{}] 체결 확인/기록 실패: {}", symbol, str(e))
            await self._mark_pending_failed(pending_record_id, str(e))
            # 체결 실패 콜백 → 예약 환불
            if on_settled:
                await on_settled(order_id, False)

    async def _cancel_unfilled_order(self, order_id: str, symbol: str) -> None:
        """미체결 주문 취소 시도"""
        if not order_id:
            return
        try:
            from trading.order_executor import order_executor
            result = await order_executor.cancel(str(order_id))
            if result.success:
                logger.debug("[{}] 미체결 주문 취소 완료: {}", symbol, order_id)
            else:
                logger.warning("[{}] 미체결 주문 취소 실패: {} — {}", symbol, order_id, result.message)
        except Exception as e:
            logger.warning("[{}] 미체결 주문 취소 오류: {} — {}", symbol, order_id, str(e))

    async def _confirm_pending_record(
        self,
        pending_record_id: str,
        symbol: str,
        side: str,
        filled_qty: int,
        filled_price: float,
        analysis_context: dict | None = None,
        exit_reason: str = "",
        cycle_id: str | None = None,
    ) -> None:
        """PENDING_CONFIRM → CONFIRMED 업데이트"""
        now = now_kst()
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    tr = await repo.filter_by_one(id=pending_record_id)
                    if not tr:
                        logger.warning("[{}] pending 레코드 {} 미발견 → 새 레코드 생성", symbol, pending_record_id)
                        await self._record_trade_result(
                            symbol=symbol, side=side, order_id="",
                            filled_qty=filled_qty, filled_price=filled_price,
                            analysis_context=analysis_context,
                            exit_reason=exit_reason, cycle_id=cycle_id,
                        )
                        return

                    # 체결 정보 업데이트
                    tr.status = OrderConfirmStatus.CONFIRMED.value
                    tr.quantity = filled_qty
                    if side == "BUY":
                        tr.entry_price = filled_price
                        tr.entry_at = tr.entry_at or now
                    elif side == "SELL":
                        tr.exit_price = filled_price
                        tr.exit_at = now
                        tr.exit_reason = exit_reason or "SIGNAL"
                        # 매도 체결 → 미청산 BUY 전체 일괄 청산
                        open_buys = await repo.get_all_open_buys(symbol)
                        for open_buy in open_buys:
                            entry_price = open_buy.entry_price
                            pnl = (filled_price - entry_price) * open_buy.quantity
                            return_pct = ((filled_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                            open_buy.exit_price = filled_price
                            open_buy.pnl = pnl
                            open_buy.return_pct = round(return_pct, 2)
                            open_buy.is_win = pnl > 0
                            open_buy.hold_days = (now - open_buy.entry_at).days if open_buy.entry_at else 0
                            open_buy.exit_reason = exit_reason or "SIGNAL"
                            open_buy.exit_at = now
                        if open_buys:
                            logger.debug("[{}] 미청산 BUY {}건 일괄 청산 완료", symbol, len(open_buys))

                    tr.notes = None  # PENDING 메모 제거
                    logger.debug("[{}] PENDING → CONFIRMED: {}주 @{:,.0f}원", symbol, filled_qty, filled_price)

                    await activity_logger.log(
                        ActivityType.TRADE_RESULT, ActivityPhase.COMPLETE,
                        f"\U0001f4dd [{symbol}] {side} 체결 확인: {filled_qty}주 @{filled_price:,.0f}원",
                        cycle_id=cycle_id, symbol=symbol,
                    )
        except Exception as e:
            logger.error("[{}] PENDING→CONFIRMED 업데이트 실패: {}", symbol, str(e))

    async def _mark_pending_failed(self, pending_record_id: str | None, reason: str) -> None:
        """PENDING_CONFIRM → CONFIRM_FAILED 마킹"""
        if not pending_record_id:
            return
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    tr = await repo.filter_by_one(id=pending_record_id)
                    if tr and tr.status == OrderConfirmStatus.PENDING_CONFIRM.value:
                        tr.status = OrderConfirmStatus.CONFIRM_FAILED.value
                        tr.notes = f"CONFIRM_FAILED: {reason[:200]}"
                        logger.warning("[{}] PENDING → CONFIRM_FAILED: {}", tr.stock_symbol, reason[:100])
        except Exception as e:
            logger.error("PENDING→FAILED 마킹 실패: {}", str(e))

    async def _record_trade_result(
        self,
        symbol: str,
        side: str,
        order_id: str,
        filled_qty: int,
        filled_price: float,
        analysis_context: dict | None = None,
        exit_reason: str = "",
        cycle_id: str | None = None,
    ) -> None:
        """체결 확인 후 TradeResult 생성/업데이트"""
        ctx = analysis_context or {}
        now = now_kst()

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)

                    # 중복 체크 (P2-7: order_id UNIQUE)
                    if order_id:
                        existing = await repo.get_by_order_id(order_id)
                        if existing:
                            logger.debug("[{}] 주문번호 {} 이미 기록됨 → 스킵", symbol, order_id)
                            return

                    if side == "BUY":
                        # 매수 체결 → 새 TradeResult 생성 (미청산 상태)
                        tr = TradeResult(
                            order_id=order_id,
                            stock_symbol=symbol,
                            stock_name=ctx.get("stock_name", symbol),
                            side="BUY",
                            strategy_type=ctx.get("strategy_type", ""),
                            entry_price=filled_price,
                            exit_price=0.0,
                            quantity=filled_qty,
                            pnl=0.0,
                            return_pct=0.0,
                            is_win=False,
                            hold_days=0,
                            ai_recommendation=ctx.get("ai_recommendation", ""),
                            ai_confidence=ctx.get("ai_confidence", 0.0),
                            ai_target_price=ctx.get("ai_target_price"),
                            ai_stop_loss_price=ctx.get("ai_stop_loss_price"),
                            entry_rsi=ctx.get("entry_rsi"),
                            entry_macd_hist=ctx.get("entry_macd_hist"),
                            market_regime=ctx.get("market_regime", ""),
                            entry_at=now,
                        )
                        session.add(tr)

                        logger.info(
                            "[TradeResult] 매수 기록 생성: {} {}주 @{:,.0f}원",
                            symbol, filled_qty, filled_price,
                        )
                        await activity_logger.log(
                            ActivityType.TRADE_RESULT, ActivityPhase.COMPLETE,
                            f"\U0001f4dd [{symbol}] 매수 체결 기록: "
                            f"{filled_qty}주 @{filled_price:,.0f}원",
                            cycle_id=cycle_id,
                            symbol=symbol,
                        )

                    elif side == "SELL":
                        # 매도 체결 → 미청산 BUY 전체 일괄 청산
                        open_buys = await repo.get_all_open_buys(symbol)
                        if not open_buys:
                            logger.warning(
                                "[TradeResult] {} 미청산 매수 기록 없음 → 매도 기록만 생성",
                                symbol,
                            )
                            # 매수 기록 없이 매도만 온 경우 → 독립 기록
                            tr = TradeResult(
                                order_id=order_id,
                                stock_symbol=symbol,
                                stock_name=ctx.get("stock_name", symbol),
                                side="SELL",
                                strategy_type=ctx.get("strategy_type", ""),
                                entry_price=0.0,
                                exit_price=filled_price,
                                quantity=filled_qty,
                                exit_reason=exit_reason or "SIGNAL",
                                exit_at=now,
                                entry_at=now,
                            )
                            session.add(tr)
                            return

                        # 모든 미청산 BUY 일괄 청산
                        total_pnl = 0.0
                        for open_buy in open_buys:
                            entry_price = open_buy.entry_price
                            pnl = (filled_price - entry_price) * open_buy.quantity
                            return_pct = ((filled_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                            is_win = pnl > 0
                            hold_days = (now - open_buy.entry_at).days if open_buy.entry_at else 0

                            open_buy.exit_price = filled_price
                            open_buy.pnl = pnl
                            open_buy.return_pct = round(return_pct, 2)
                            open_buy.is_win = is_win
                            open_buy.hold_days = hold_days
                            open_buy.exit_reason = exit_reason or "SIGNAL"
                            open_buy.exit_at = now
                            total_pnl += pnl

                        # 마지막 BUY 기준으로 로깅
                        last_buy = open_buys[-1]
                        pnl_sign = "+" if total_pnl >= 0 else ""
                        avg_return = sum(
                            ((filled_price - ob.entry_price) / ob.entry_price * 100)
                            for ob in open_buys if ob.entry_price > 0
                        ) / len(open_buys)
                        logger.info(
                            "[TradeResult] 매도 청산: {} {}건 BUY 일괄 청산@{:,.0f} "
                            "= {}{:,.0f}원 ({}{:.1f}%)",
                            symbol, len(open_buys), filled_price,
                            pnl_sign, total_pnl, pnl_sign, avg_return,
                        )
                        await activity_logger.log(
                            ActivityType.TRADE_RESULT, ActivityPhase.COMPLETE,
                            f"{'✅' if total_pnl > 0 else '❌'} [{symbol}] 매도 청산: "
                            f"{len(open_buys)}건 BUY 일괄 — "
                            f"{pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{avg_return:.1f}%) "
                            f"| {exit_reason or 'SIGNAL'}",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            detail={
                                "closed_count": len(open_buys),
                                "exit_price": filled_price,
                                "total_pnl": total_pnl,
                                "avg_return_pct": avg_return,
                            },
                        )

        except Exception as e:
            logger.error("[TradeResult] 기록 실패 ({}): {}", symbol, str(e))

    async def _create_recommendation(
        self, signal: TradeSignal, analysis_id: str, cycle_id: str | None = None,
    ) -> dict:
        """반자율: 추천 생성 → 사용자 승인 대기"""
        expires_at = now_kst() + timedelta(minutes=settings.RECOMMENDATION_EXPIRE_MIN)

        rec_data = {
            "stock_id": signal.stock_id,
            "analysis_id": analysis_id,
            "action": signal.action.value,
            "suggested_price": signal.suggested_price or 0,
            "suggested_quantity": signal.suggested_quantity or 0,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "status": RecommendationStatus.PENDING.value,
            "expires_at": expires_at,
        }

        qty = signal.suggested_quantity or 0
        price = signal.suggested_price or 0
        amount = price * qty

        logger.info(
            "[SEMI_AUTO] 추천 생성: {} {} x{} (만료: {})",
            signal.symbol, signal.action.value,
            qty, expires_at,
        )

        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.COMPLETE,
            f"\U0001f4dd 매수 추천 생성: {signal.symbol} {qty}주 "
            f"@{price:,.0f}원 ({amount:,.0f}원)"
            f"\n   \u2192 사용자 승인 대기 (SEMI_AUTO 모드)",
            cycle_id=cycle_id,
            symbol=signal.symbol,
            confidence=signal.confidence,
            detail=rec_data,
        )

        await event_bus.publish(Event(
            type=EventType.RECOMMENDATION_CREATED,
            data={**rec_data, "symbol": signal.symbol},
            source="decision_maker",
        ))

        return {
            "mode": "SEMI_AUTO",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "recommendation": rec_data,
        }


decision_maker = DecisionMaker()
