"""매매 결과 리포지토리"""
from datetime import date, datetime, time

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade_result import TradeResult
from repositories.async_base_repository import AsyncBaseRepository
from util.time_util import KST


class TradeResultRepository(AsyncBaseRepository[TradeResult]):

    def __init__(self, session: AsyncSession):
        super().__init__(TradeResult, session)

    async def get_by_symbol(self, symbol: str, limit: int = 50) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.stock_symbol == symbol)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_strategy(self, strategy_type: str, limit: int = 100) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.strategy_type == strategy_type)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 50) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_open_buy(self, symbol: str) -> TradeResult | None:
        """미청산 매수 기록 조회 (exit_at IS NULL, side=BUY, status=CONFIRMED)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.stock_symbol == symbol,
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_open_buys(self, symbol: str) -> list[TradeResult]:
        """특정 종목의 미청산 BUY 전체 조회 (entry_at 오름차순=FIFO; close_open_buys_fifo에서 사용)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.stock_symbol == symbol,
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.entry_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def close_open_buys_fifo(
        self,
        symbol: str,
        sell_qty: int,
        sell_price: float,
        exit_reason: str,
        sell_order_id: str | None,
        now: datetime,
    ) -> tuple[float, int]:
        """매도 체결을 미청산 매수에 FIFO(진입 오래된 순)·수량 기준으로 매칭 청산.

        - 전량 소진 매수 → 그 행을 청산 처리(매도가·시각·사유·sell_order_id·순손익 기록)
        - 부분 소진 매수 → 분할: 소진분을 별도 청산 행으로 추가하고 원 매수는 잔량만 유지
        sell_qty<=0이면 미청산 전량을 청산(수량 정보 없는 안전망).

        Returns: (총 순손익, 실제 청산 수량)
        """
        from util.pnl_calculator import compute_pnl
        from util.time_util import ensure_kst

        open_buys = await self.get_all_open_buys(symbol)  # entry_at asc (FIFO)
        remaining = sell_qty if sell_qty and sell_qty > 0 else sum(b.quantity for b in open_buys)
        reason = exit_reason or "SIGNAL"
        total_pnl = 0.0
        closed_qty = 0

        for buy in open_buys:
            if remaining <= 0:
                break
            take = min(buy.quantity, remaining)
            br = compute_pnl(
                entry_price=buy.entry_price, exit_price=sell_price, qty=take,
                market=buy.market or "KOSPI", stock_name=buy.stock_name or "",
            )
            hold_days = (now - ensure_kst(buy.entry_at)).days if buy.entry_at else 0

            if take == buy.quantity:
                # 전량 청산
                buy.exit_price = sell_price
                buy.pnl = br.net_pnl
                buy.return_pct = br.return_pct
                buy.is_win = br.is_win
                buy.commission_amt = br.commission
                buy.tax_amt = br.tax
                buy.hold_days = hold_days
                buy.exit_reason = reason
                buy.exit_at = now
                buy.sell_order_id = sell_order_id
            else:
                # 부분 청산 — 소진분을 별도 청산 행으로 분할, 원 매수는 잔량 유지
                self.db.add(TradeResult(
                    stock_symbol=buy.stock_symbol,
                    stock_name=buy.stock_name,
                    side="BUY",
                    strategy_type=buy.strategy_type,
                    entry_price=buy.entry_price,
                    exit_price=sell_price,
                    quantity=take,
                    pnl=br.net_pnl,
                    return_pct=br.return_pct,
                    is_win=br.is_win,
                    commission_amt=br.commission,
                    tax_amt=br.tax,
                    hold_days=hold_days,
                    exit_reason=reason,
                    sell_order_id=sell_order_id,
                    ai_recommendation=buy.ai_recommendation,
                    ai_confidence=buy.ai_confidence,
                    ai_target_price=buy.ai_target_price,
                    ai_stop_loss_price=buy.ai_stop_loss_price,
                    market=buy.market,
                    market_regime=buy.market_regime,
                    status=buy.status,
                    entry_at=buy.entry_at,
                    exit_at=now,
                ))
                buy.quantity -= take

            total_pnl += br.net_pnl
            closed_qty += take
            remaining -= take

        await self.db.flush()
        return total_pnl, closed_qty

    async def get_completed_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜에 청산 완료된 포지션 (BUY→청산 기록만, CONFIRMED)

        SELL 레코드가 아닌, 청산된 BUY 레코드를 반환.
        이 레코드에 pnl, return_pct, is_win이 정확히 기록되어 있음.
        """
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.exit_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_sell_count_by_date(self, target_date: date) -> int:
        """특정 날짜의 매도 주문 건수 (SELL 레코드 수, CONFIRMED)"""
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(func.count())
            .select_from(TradeResult)
            .where(and_(
                TradeResult.side == "SELL",
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def get_sells_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜 SELL 레코드 전체 (BUY-SELL 매칭 여부 무관, CONFIRMED만)

        리포트 누락 가시화용 — `get_completed_by_date`가 status=CONFIRMED인 BUY만
        잡아 BUY가 CONFIRM_FAILED인 경우(폴링 race) 매도가 리포트에서 누락됐다.
        SELL 레코드 직접 조회로 매도된 종목을 빠짐없이 가시화한다.
        """
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "SELL",
                TradeResult.exit_at.isnot(None),
                TradeResult.exit_at >= start,
                TradeResult.exit_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.exit_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_opened_by_date(self, target_date: date) -> list[TradeResult]:
        """특정 날짜에 진입한 매수 기록 (entry_at 기준, CONFIRMED만)"""
        start = datetime.combine(target_date, time.min, tzinfo=KST)
        end = datetime.combine(target_date, time.max, tzinfo=KST)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.entry_at >= start,
                TradeResult.entry_at <= end,
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.entry_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_open(self) -> list[TradeResult]:
        """미청산 포지션 전체 조회 (exit_at IS NULL, side=BUY, status=CONFIRMED)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.exit_at.is_(None),
                TradeResult.status == "CONFIRMED",
            ))
            .order_by(TradeResult.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_order_id(
        self,
        order_id: str,
        symbol: str | None = None,
        side: str | None = None,
        since=None,
    ) -> TradeResult | None:
        """주문번호로 TradeResult 조회 (최신 순)

        KIS 주문번호(odno)는 일 단위로만 유일 — 중복 판정 시 symbol/side/since를
        함께 넘겨 같은 주문인지 좁혀야 함 (과거 다른 종목의 동일 번호 오탐 방지).
        """
        if not order_id:
            return None
        stmt = select(TradeResult).where(TradeResult.order_id == order_id)
        if symbol:
            stmt = stmt.where(TradeResult.stock_symbol == symbol)
        if side:
            stmt = stmt.where(TradeResult.side == side)
        if since is not None:
            stmt = stmt.where(TradeResult.created_at >= since)
        stmt = stmt.order_by(TradeResult.created_at.desc()).limit(1)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_confirms(self) -> list[TradeResult]:
        """PENDING_CONFIRM 상태 레코드 조회 (복구용)"""
        stmt = (
            select(TradeResult)
            .where(TradeResult.status == "PENDING_CONFIRM")
            .order_by(TradeResult.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_rolling_breakdown(self, days: int = 21) -> dict:
        """롤링 성과 분해 — 일일 리뷰 프롬프트 주입용 (하루 표본 과잉반응 방지)

        Returns:
            {"total": {...}, "by_strategy": {...}, "by_hold_time": {...}, "by_confidence": {...}}
        """
        from datetime import timedelta
        from util.time_util import now_kst

        cutoff = now_kst() - timedelta(days=days)
        stmt = (
            select(TradeResult)
            .where(and_(
                TradeResult.side == "BUY",
                TradeResult.status == "CONFIRMED",
                TradeResult.exit_at.isnot(None),
                TradeResult.entry_at >= cutoff.replace(tzinfo=None),
            ))
        )
        trades = list((await self.db.execute(stmt)).scalars().all())

        def _agg(items) -> dict:
            n = len(items)
            if n == 0:
                return {"n": 0, "win_rate": 0.0, "pnl": 0.0, "avg_return": 0.0}
            return {
                "n": n,
                "win_rate": round(100.0 * sum(1 for t in items if t.is_win) / n, 1),
                "pnl": round(sum(t.pnl or 0 for t in items)),
                "avg_return": round(sum(t.return_pct or 0 for t in items) / n, 2),
            }

        def _hold_bucket(t) -> str:
            if not t.exit_at or not t.entry_at:
                return "기타"
            hours = (t.exit_at - t.entry_at).total_seconds() / 3600
            if hours < 0.5:
                return "30분 미만"
            if hours < 2:
                return "30분~2시간"
            if hours < 8:
                return "2~8시간"
            return "오버나이트+"

        def _conf_bucket(t) -> str:
            c = t.ai_confidence or 0.0
            if c >= 0.65:
                return "0.65+"
            if c >= 0.60:
                return "0.60~0.64"
            if c >= 0.55:
                return "0.55~0.59"
            return "0.55 미만"

        by_strategy: dict[str, list] = {}
        by_hold: dict[str, list] = {}
        by_conf: dict[str, list] = {}
        for t in trades:
            by_strategy.setdefault(t.strategy_type or "N/A", []).append(t)
            by_hold.setdefault(_hold_bucket(t), []).append(t)
            by_conf.setdefault(_conf_bucket(t), []).append(t)

        return {
            "days": days,
            "total": _agg(trades),
            "by_strategy": {k: _agg(v) for k, v in by_strategy.items()},
            "by_hold_time": {k: _agg(v) for k, v in by_hold.items()},
            "by_confidence": {k: _agg(v) for k, v in by_conf.items()},
        }
