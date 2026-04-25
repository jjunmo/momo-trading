"""장 마감 후 포트폴리오 정산 — PENDING_CONFIRM 복구 + 계좌/DB 불일치 점검"""
from loguru import logger


async def portfolio_sync_job() -> None:
    """KIS 계좌와 포트폴리오 DB 동기화"""
    logger.debug("포트폴리오 정산 시작")

    # 1. PENDING_CONFIRM 복구: 체결 확인 누락된 주문 재확인
    await _recover_pending_confirms()

    # 2. 정산 현황 로깅 (계좌 vs DB 비교, 강제 변경 없음)
    await _check_account_db_consistency()

    logger.debug("포트폴리오 정산 완료")


async def _recover_pending_confirms() -> None:
    """PENDING_CONFIRM 상태 레코드 복구 — 체결 여부 재확인"""
    try:
        from core.database import AsyncSessionLocal
        from repositories.trade_result_repository import TradeResultRepository
        from trading.enums import OrderConfirmStatus
        from trading.mcp_client import mcp_client

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = TradeResultRepository(session)
                pending = await repo.get_pending_confirms()

                if not pending:
                    return

                logger.debug("PENDING_CONFIRM 복구 대상: {}건", len(pending))

                # 오늘 주문내역 한 번 조회
                resp = await mcp_client.get_order_list()
                orders = []
                if resp.success and isinstance(resp.data, dict):
                    orders = (
                        resp.data.get("output", [])
                        or resp.data.get("output1", [])
                        or resp.data.get("orders", [])
                    )
                    if isinstance(orders, dict):
                        orders = [orders]
                elif resp.success and isinstance(resp.data, list):
                    orders = resp.data

                # order_id → 체결 정보 매핑
                order_map = {}
                for order in orders:
                    if not isinstance(order, dict):
                        continue
                    odno = (
                        order.get("odno") or order.get("ODNO")
                        or order.get("order_id") or ""
                    )
                    if odno:
                        order_map[str(odno)] = order

                recovered = 0
                failed = 0
                for tr in pending:
                    matched = order_map.get(str(tr.order_id))
                    if matched:
                        filled_qty = int(
                            matched.get("tot_ccld_qty")
                            or matched.get("filled_quantity")
                            or matched.get("ccld_qty")
                            or 0
                        )
                        if filled_qty > 0:
                            tr.status = OrderConfirmStatus.CONFIRMED.value
                            tr.quantity = filled_qty
                            filled_price = float(
                                matched.get("avg_prvs")
                                or matched.get("ccld_pric")
                                or matched.get("filled_price")
                                or 0
                            )
                            if filled_price > 0:
                                if tr.side == "BUY":
                                    tr.entry_price = filled_price
                                elif tr.side == "SELL":
                                    tr.exit_price = filled_price
                            tr.notes = None
                            recovered += 1
                            logger.debug(
                                "PENDING 복구: {} {} {}주 → CONFIRMED",
                                tr.stock_symbol, tr.side, filled_qty,
                            )
                        else:
                            tr.status = OrderConfirmStatus.CONFIRM_FAILED.value
                            tr.notes = "CONFIRM_FAILED: 체결수량 0 (정산 시 복구)"
                            failed += 1
                            # 미체결 주문 취소 시도
                            await _cancel_unfilled_order(str(tr.order_id), tr.stock_symbol)
                    else:
                        tr.status = OrderConfirmStatus.CONFIRM_FAILED.value
                        tr.notes = "CONFIRM_FAILED: 주문내역에서 미발견 (정산 시 복구)"
                        failed += 1
                        # 미체결 주문 취소 시도
                        await _cancel_unfilled_order(str(tr.order_id), tr.stock_symbol)

                if recovered or failed:
                    logger.debug(
                        "PENDING_CONFIRM 복구 결과: 성공 {}건, 실패 {}건",
                        recovered, failed,
                    )
    except Exception as e:
        logger.error("PENDING_CONFIRM 복구 오류: {}", str(e))


async def _cancel_unfilled_order(order_id: str, symbol: str) -> None:
    """미체결 주문 취소 시도 (정산용)"""
    if not order_id:
        return
    try:
        from trading.order_executor import order_executor
        result = await order_executor.cancel(order_id)
        if result.success:
            logger.debug("[정산] {} 미체결 주문 취소 완료: {}", symbol, order_id)
        else:
            msg = result.message or ""
            # "원주문정보가 존재하지 않습니다" = 이미 해결된 주문 (앞선 타임아웃 취소 등) → DEBUG
            if "원주문정보" in msg and "존재하지" in msg:
                logger.debug("[정산] {} 주문 이미 해결됨: {} — {}", symbol, order_id, msg)
            else:
                logger.warning("[정산] {} 미체결 주문 취소 실패: {} — {}", symbol, order_id, msg)
    except Exception as e:
        logger.warning("[정산] {} 미체결 주문 취소 오류: {} — {}", symbol, order_id, str(e))


async def _check_account_db_consistency() -> None:
    """계좌 보유종목 vs DB 미청산 포지션 비교 → 불일치 자동 청산"""
    try:
        from core.database import AsyncSessionLocal
        from models.trade_result import TradeResult
        from repositories.trade_result_repository import TradeResultRepository
        from trading.account_manager import account_manager
        from util.time_util import ensure_kst, now_kst
        from sqlalchemy import select, and_

        holdings = await account_manager.get_holdings()
        holding_symbols = {h.symbol for h in holdings if h.quantity > 0}

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open()
                db_symbols = {tr.stock_symbol for tr in open_positions}

                # 계좌에만 있는 종목 (DB에 기록 없음)
                only_account = holding_symbols - db_symbols
                # DB에만 있는 종목 (계좌에 없음)
                only_db = db_symbols - holding_symbols

                if only_account:
                    logger.warning(
                        "계좌에만 존재 (DB 미등록): {} — 수동 확인 필요",
                        ", ".join(only_account),
                    )

                # DB에만 있는 종목 → SELL 기록이 있으면 BUY 자동 청산
                if only_db:
                    now = now_kst()
                    for symbol in only_db:
                        # 해당 종목의 SELL 기록 확인 (매도 체결가 조회)
                        sell_result = await session.execute(
                            select(TradeResult).where(and_(
                                TradeResult.stock_symbol == symbol,
                                TradeResult.side == "SELL",
                                TradeResult.exit_price > 0,
                            )).order_by(TradeResult.created_at.desc())
                        )
                        sell_record = sell_result.scalars().first()

                        # 미청산 BUY 조회
                        open_buys = [
                            tr for tr in open_positions
                            if tr.stock_symbol == symbol
                        ]

                        if sell_record and open_buys:
                            # SELL 체결가로 BUY 일괄 청산 (수수료/세금 차감 net_pnl)
                            from util.pnl_calculator import compute_pnl
                            sell_price = sell_record.exit_price
                            for buy in open_buys:
                                br = compute_pnl(
                                    entry_price=buy.entry_price,
                                    exit_price=sell_price,
                                    qty=buy.quantity,
                                    market=buy.market or "KOSPI",
                                )
                                buy.exit_price = sell_price
                                buy.pnl = br.net_pnl
                                buy.return_pct = br.return_pct
                                buy.is_win = br.is_win
                                buy.commission_amt = br.commission
                                buy.tax_amt = br.tax
                                buy.hold_days = (now - ensure_kst(buy.entry_at)).days if buy.entry_at else 0
                                buy.exit_reason = buy.exit_reason or "SYNC_CLOSE"
                                buy.exit_at = sell_record.exit_at or now
                            logger.info(
                                "[정산] {} 미청산 BUY {}건 → SELL 체결가({:,.0f}원)로 자동 청산",
                                symbol, len(open_buys), sell_price,
                            )
                        elif open_buys:
                            # SELL 기록 없음 → 경고만 (함부로 삭제하지 않음)
                            logger.warning(
                                "DB에만 존재 (계좌 미보유, SELL 기록 없음): {} — 수동 확인 필요",
                                symbol,
                            )

                if not only_account and not only_db:
                    logger.debug("계좌/DB 포지션 일치 ({}종목)", len(holding_symbols))
    except Exception as e:
        logger.error("계좌/DB 일관성 체크 오류: {}", str(e))
