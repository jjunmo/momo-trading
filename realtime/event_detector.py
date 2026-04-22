"""PriceGuard — 보유종목 실시간 가격 감시 + 안전장치

보유종목만 감시. LLM이 설정한 손절/익절/트레일링 수치 기반.
- 손절 도달 → 즉시 매도 (안전장치)
- 익절 도달 → StockAnalysisAgent 재분석 트리거
- 트레일링 스탑 → 기계적 고점 추적
- 가격 변동 추적 → 재평가 주기 조절 (review_threshold_pct 기반)
"""
import math
from dataclasses import dataclass

from loguru import logger


@dataclass
class StockThresholds:
    """보유종목 감시 임계값 (LLM 분석 결과에서 설정)"""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop_pct: float = 0.0

    # 트레일링 스탑용
    highest_price: float = 0.0
    entry_price: float = 0.0
    initial_take_profit: float = 0.0
    initial_stop_loss: float = 0.0
    breakeven_trigger_pct: float = 0.0

    # 재평가 주기 조절 (LLM이 ATR 기반 결정, 가격 변동 트리거)
    review_threshold_pct: float = 0.0

    # 시간 기반 재평가 주기 (LLM이 결정, 다음 재평가 시각)
    next_review_at: float = 0.0  # time.time() 기준 epoch seconds


DEFAULT_THRESHOLDS = StockThresholds()


class PriceGuard:
    """보유종목 실시간 가격 감시 + 안전장치

    매수 체결된 종목만 등록. LLM이 설정한 수치를 그대로 실행.
    자체 판단 없음 — 손절/익절/트레일링/재평가 기준 모두 LLM이 결정.
    """

    def __init__(self):
        self._thresholds: dict[str, StockThresholds] = {}
        self._prev_prices: dict[str, float] = {}
        # 재평가 주기 조절용 누적 변동
        self._movement_score: dict[str, float] = {}
        # 익절 재분석 쿨다운 (종목별 진행 중 플래그)
        self._review_in_progress: set[str] = set()

    # ── 임계값 관리 ──

    def set_thresholds(self, symbol: str, **kwargs) -> None:
        """보유종목 감시 임계값 설정 (매수 체결 시 BuyAgent가 호출)

        review_interval_min을 넘기면 now + (min * 60)으로 next_review_at 자동 계산.
        직접 next_review_at을 넘기면 그 값을 사용 (DB 복원 등).
        """
        import time as _time
        # review_interval_min → next_review_at 변환
        if "review_interval_min" in kwargs:
            mins = kwargs.pop("review_interval_min")
            try:
                mins_f = float(mins)
                if mins_f > 0:
                    kwargs["next_review_at"] = _time.time() + mins_f * 60
            except (TypeError, ValueError):
                pass

        validated = {}
        for k, v in kwargs.items():
            if isinstance(v, str):
                validated[k] = v
            elif isinstance(v, (int, float)) and not math.isnan(v):
                validated[k] = v

        if symbol in self._thresholds:
            th = self._thresholds[symbol]
            for k, v in validated.items():
                if hasattr(th, k):
                    setattr(th, k, v)
        else:
            self._thresholds[symbol] = StockThresholds(**validated)

        # trailing_stop 설정 시 highest_price 초기화
        th = self._thresholds[symbol]
        if 0 < th.trailing_stop_pct < 100 and th.highest_price == 0 and th.stop_loss > 0:
            th.highest_price = th.stop_loss / (1 - th.trailing_stop_pct / 100)

    def get_thresholds(self, symbol: str) -> StockThresholds:
        return self._thresholds.get(symbol, DEFAULT_THRESHOLDS)

    def remove_levels(self, symbol: str) -> None:
        self._thresholds.pop(symbol, None)
        self._prev_prices.pop(symbol, None)
        self._movement_score.pop(symbol, None)
        self._review_in_progress.discard(symbol)

    def clear_all(self) -> None:
        self._thresholds.clear()
        self._prev_prices.clear()
        self._review_in_progress.clear()
        self._movement_score.clear()

    @property
    def monitored_symbols(self) -> list[str]:
        return list(self._thresholds.keys())

    # ── 실시간 가격 처리 ──

    async def on_price_update(self, data: dict) -> None:
        """WebSocket 가격 업데이트 → 안전장치 + 변동 추적"""
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_domestic_trading_hours():
            return

        symbol = data.get("symbol", "")
        price = data.get("price", 0)
        if not symbol or price <= 0:
            return

        th = self._thresholds.get(symbol)
        if not th:
            return  # 미등록 종목 → 무시

        # 1. 트레일링 스탑 추적
        if th.trailing_stop_pct > 0:
            if th.entry_price > 0:
                profit_pct = (price - th.entry_price) / th.entry_price * 100
                if (th.breakeven_trigger_pct > 0
                        and profit_pct >= th.breakeven_trigger_pct
                        and th.stop_loss < th.entry_price):
                    th.stop_loss = th.entry_price
                    logger.info("본전 보호 활성: {} 손절 → {:,.0f}원 (수익률 {:.1f}%)",
                                symbol, th.entry_price, profit_pct)

            if price > th.highest_price:
                th.highest_price = price
                new_stop = price * (1 - th.trailing_stop_pct / 100)
                if new_stop > th.stop_loss:
                    th.stop_loss = new_stop
                    logger.debug("트레일링 스탑 상향: {} 손절 {:,.0f}원 (고점 {:,.0f})",
                                 symbol, new_stop, price)

        # 2. 손절/익절 안전장치
        await self._check_stop_take(symbol, price, th)

        # 3. 가격 변동 추적 → 재평가 주기 조절
        prev = self._prev_prices.get(symbol)
        if prev and prev > 0:
            change_pct = abs(price - prev) / prev * 100
            self._movement_score[symbol] = self._movement_score.get(symbol, 0) + change_pct
        self._prev_prices[symbol] = price

    # ── 손절/익절 안전장치 ──

    async def _check_stop_take(
        self, symbol: str, price: float, th: StockThresholds,
    ) -> None:
        """손절 → 즉시 매도 / 익절 → 재분석 트리거"""
        # 손절 도달 → SellAgent에 매도 요청
        if th.stop_loss > 0 and price <= th.stop_loss:
            is_trailing = (th.trailing_stop_pct > 0
                           and th.initial_stop_loss > 0
                           and th.stop_loss > th.initial_stop_loss)
            label = "트레일링 스탑" if is_trailing else "손절선"
            exit_reason = "TRAILING_STOP" if is_trailing else "STOP_LOSS"
            logger.warning("{} 도달: {} (현재 {:,.0f}, 손절 {:,.0f})",
                           label, symbol, price, th.stop_loss)
            from agent.sell_agent import SellParams, sell_agent
            import asyncio
            asyncio.create_task(sell_agent.execute_sell(SellParams(symbol=symbol, exit_reason=exit_reason)))

        # 익절 도달 → 재분석 트리거 (즉시 매도 대신, 쿨다운 적용)
        elif th.take_profit > 0 and price >= th.take_profit:
            if symbol not in self._review_in_progress:
                logger.info("익절선 도달: {} (현재 {:,.0f}, 익절 {:,.0f}) → 재분석 트리거",
                            symbol, price, th.take_profit)
                await self._trigger_take_profit_review(symbol, price)

    async def _trigger_take_profit_review(self, symbol: str, price: float) -> None:
        """익절 도달 → 재분석 요청 (PriceGuard는 분석하지 않음)

        StockAnalysisAgent에 분석 요청 → 결과를 SellAgent에 전달.
        PriceGuard는 트리거만 하고 분석/실행은 각 Agent가 담당.
        """
        import asyncio
        asyncio.create_task(self._request_review(symbol))

    async def _request_review(self, symbol: str) -> None:
        """분석 Agent에 재분석 요청 → 결과를 매도 Agent에 전달"""
        self._review_in_progress.add(symbol)
        try:
            from agent.stock_analysis_agent import StockAnalysisRequest, stock_analysis_agent
            from trading.account_manager import account_manager

            holdings = await account_manager.get_holdings()
            holding = next((h for h in holdings if h.symbol == symbol), None)
            if not holding or holding.quantity <= 0:
                return

            th = self.get_thresholds(symbol)

            request = StockAnalysisRequest(
                symbol=symbol,
                name=holding.name or symbol,
                is_holding=True,
                purpose="TAKE_PROFIT_REVIEW",
                avg_price=holding.avg_buy_price,
                pnl_rate=holding.pnl_rate,
                quantity=holding.quantity,
                active_stop_loss=th.stop_loss,
                active_take_profit=th.take_profit,
                active_trailing_stop_pct=th.trailing_stop_pct,
            )

            result = await stock_analysis_agent.analyze(request, force=True)
            if not result.success:
                return

            # 분석 결과에 따라 라우팅
            if result.recommendation == "SELL":
                from agent.sell_agent import SellParams, sell_agent
                await sell_agent.execute_sell(SellParams(symbol=symbol, exit_reason="TAKE_PROFIT_REVIEW"))
            elif result.recommendation == "BUY":
                from agent.buy_agent import BuyParams, buy_agent
                await buy_agent.execute(BuyParams(
                    symbol=symbol, name=holding.name or symbol,
                    strategy_type="STABLE_SHORT", price=result.current_price,
                    confidence=result.confidence, reason=result.reason,
                    stop_loss_price=result.stop_loss_price,
                    take_profit_price=result.target_price,
                    trailing_stop_pct=result.trailing_stop_pct,
                    breakeven_trigger_pct=result.breakeven_trigger_pct,
                    review_threshold_pct=result.review_threshold_pct,
                ))
            # HOLD → 임계값은 StockAnalysisAgent가 분석 시 직접 설정 완료

        except Exception as e:
            logger.error("익절 재분석 요청 실패 ({}): {}", symbol, str(e))
        finally:
            self._review_in_progress.discard(symbol)

    # ── 재평가 주기 조절 ──

    def needs_urgent_review(self, symbol: str) -> bool:
        """누적 변동이 LLM 설정 기준 초과 → 즉시 재평가 필요"""
        th = self._thresholds.get(symbol)
        if not th or th.review_threshold_pct <= 0:
            return False
        score = self._movement_score.get(symbol, 0)
        return score >= th.review_threshold_pct

    def reset_movement_score(self, symbol: str) -> None:
        """재평가 완료 후 누적 변동 초기화"""
        self._movement_score[symbol] = 0.0

    def get_urgent_symbols(self) -> list[str]:
        """즉시 재평가가 필요한 종목 목록"""
        return [s for s in self._thresholds if self.needs_urgent_review(s)]


price_guard = PriceGuard()
# 하위 호환
event_detector = price_guard
