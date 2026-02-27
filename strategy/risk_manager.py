"""리스크 관리 - 포지션/손절/비중/일일한도"""
from loguru import logger

from core.config import settings
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import SignalAction


class RiskManager:
    """
    리스크 관리자
    - 포지션 크기 제한
    - 총 비중 제한
    - 손절 검사
    - 일일 매매 한도
    - 단일 주문 금액 한도
    """

    def __init__(self):
        self.max_daily_trades = settings.MAX_DAILY_TRADES
        self.max_single_order_krw = settings.MAX_SINGLE_ORDER_KRW
        self.max_single_order_usd = settings.MAX_SINGLE_ORDER_USD
        self.min_cash_ratio = settings.MIN_CASH_RATIO  # 기본 5%

    async def check(
        self,
        signal: TradeSignal,
        portfolio_cash: float,
        portfolio_budget: float,
        today_trade_count: int,
        current_holding_count: int,
        max_position_pct: float = 20.0,
        cycle_id: str | None = None,
        dynamic_limits: dict | None = None,
    ) -> dict:
        """
        리스크 검사

        Args:
            dynamic_limits: AI가 결정한 동적 한도 (있으면 기본값 대신 사용)

        Returns:
            {"approved": bool, "reason": str, "adjusted_quantity": int | None}
        """
        symbol = signal.symbol

        # 동적 한도 적용 (AI 결정값 또는 기본값)
        eff_max_daily = self.max_daily_trades
        eff_max_order = self.max_single_order_krw
        eff_min_qty = settings.MIN_BUY_QUANTITY
        eff_min_cash_ratio = self.min_cash_ratio
        eff_max_pos_pct = max_position_pct

        if dynamic_limits:
            eff_max_daily = dynamic_limits.get("max_daily_trades", eff_max_daily)
            eff_max_order = dynamic_limits.get("max_single_order_krw", eff_max_order)
            eff_min_qty = dynamic_limits.get("min_buy_quantity", eff_min_qty)
            eff_min_cash_ratio = dynamic_limits.get("min_cash_ratio", eff_min_cash_ratio)
            eff_max_pos_pct = dynamic_limits.get("max_position_pct", eff_max_pos_pct)

        # 매도는 기본적으로 허용
        if signal.action == SignalAction.SELL:
            result = {"approved": True, "reason": "매도 주문", "adjusted_quantity": None}
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        # 매매 비활성화 검사
        if not settings.TRADING_ENABLED:
            result = {"approved": False, "reason": "매매가 비활성화되어 있습니다"}
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        # 일일 매매 한도 검사
        if today_trade_count >= eff_max_daily:
            logger.warning("일일 매매 한도 초과: {}/{}", today_trade_count, eff_max_daily)
            result = {"approved": False, "reason": f"일일 매매 한도 초과 ({eff_max_daily}회)"}
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        # 주문 금액 계산
        price = signal.suggested_price or 0
        quantity = signal.suggested_quantity or 0
        if price <= 0 or quantity <= 0:
            result = {"approved": False, "reason": "가격 또는 수량이 유효하지 않습니다"}
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        total_amount = price * quantity

        # 리스크:보상 비율 검사 (다른 조정 전에 먼저 확인)
        entry = signal.suggested_price or 0
        target = signal.target_price or 0
        stop = signal.stop_loss_price or 0

        if entry > 0 and target > 0 and stop > 0:
            reward = abs(target - entry)
            risk = abs(entry - stop)
            if risk > 0:
                rr_ratio = reward / risk
                if rr_ratio < 1.5:
                    result = {
                        "approved": False,
                        "reason": f"리스크:보상 비율 부족 ({rr_ratio:.1f}:1, 최소 1.5:1 필요)",
                        "adjusted_quantity": None,
                    }
                    await self._log_result(symbol, result, today_trade_count, cycle_id)
                    return result

        # 단일 주문 금액 한도 (0이면 AI 자율 → 스킵)
        if eff_max_order > 0 and total_amount > eff_max_order:
            adjusted_qty = int(eff_max_order / price)
            if adjusted_qty < eff_min_qty:
                result = {"approved": False, "reason": "단일 주문 한도 내에서 최소 수량 미달"}
                await self._log_result(symbol, result, today_trade_count, cycle_id)
                return result
            result = {
                "approved": True,
                "reason": f"수량 조정 (한도 초과): {quantity} → {adjusted_qty}",
                "adjusted_quantity": adjusted_qty,
            }
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        # 현금 부족 검사
        if total_amount > portfolio_cash:
            adjusted_qty = int(portfolio_cash / price)
            if adjusted_qty < eff_min_qty:
                result = {"approved": False, "reason": "현금 부족"}
                await self._log_result(symbol, result, today_trade_count, cycle_id)
                return result
            result = {
                "approved": True,
                "reason": f"수량 조정 (현금 부족): {quantity} → {adjusted_qty}",
                "adjusted_quantity": adjusted_qty,
            }
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        # 최소 현금 비중 검사
        cash_after = portfolio_cash - total_amount
        if portfolio_budget > 0 and cash_after / portfolio_budget < eff_min_cash_ratio:
            max_spend = portfolio_cash - (portfolio_budget * eff_min_cash_ratio)
            if max_spend <= 0:
                result = {"approved": False, "reason": "현금 비중 최소 한도 미달"}
                await self._log_result(symbol, result, today_trade_count, cycle_id)
                return result
            adjusted_qty = int(max_spend / price)
            if adjusted_qty < eff_min_qty:
                result = {"approved": False, "reason": "현금 비중 유지 후 최소 수량 미달"}
                await self._log_result(symbol, result, today_trade_count, cycle_id)
                return result
            result = {
                "approved": True,
                "reason": f"수량 조정 (현금 비중 유지): {quantity} → {adjusted_qty}",
                "adjusted_quantity": adjusted_qty,
            }
            await self._log_result(symbol, result, today_trade_count, cycle_id)
            return result

        # 종목 비중 검사
        if portfolio_budget > 0:
            position_pct = (total_amount / portfolio_budget) * 100
            if position_pct > eff_max_pos_pct:
                adjusted_qty = int((portfolio_budget * eff_max_pos_pct / 100) / price)
                if adjusted_qty < eff_min_qty:
                    result = {"approved": False, "reason": "비중 한도 내에서 최소 수량 미달"}
                    await self._log_result(symbol, result, today_trade_count, cycle_id)
                    return result
                result = {
                    "approved": True,
                    "reason": f"수량 조정 (비중 한도): {quantity} → {adjusted_qty}",
                    "adjusted_quantity": adjusted_qty,
                }
                await self._log_result(symbol, result, today_trade_count, cycle_id)
                return result

        result = {"approved": True, "reason": "리스크 검사 통과", "adjusted_quantity": None}
        await self._log_result(symbol, result, today_trade_count, cycle_id)
        return result

    async def _log_result(
        self, symbol: str, result: dict, today_trade_count: int, cycle_id: str | None
    ) -> None:
        approved = result.get("approved", False)
        reason = result.get("reason", "")

        if approved:
            summary = (
                f"\U0001f6e1\ufe0f [{symbol}] 리스크 검사 통과"
                f"\n   일일거래: {today_trade_count}/{self.max_daily_trades} | {reason}"
            )
        else:
            summary = f"\U0001f6e1\ufe0f [{symbol}] 리스크 검사 미통과: {reason}"

        await activity_logger.log(
            "RISK_CHECK", "COMPLETE",
            summary,
            cycle_id=cycle_id,
            symbol=symbol,
            detail=result,
        )


risk_manager = RiskManager()
