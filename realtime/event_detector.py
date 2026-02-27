"""실시간 이벤트 감지 — 종목별 AI 설정 임계값 기반"""
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from core.events import Event, EventType, event_bus


@dataclass
class StockThresholds:
    """종목별 감시 임계값 (AI가 종목 선정 시 설정)"""
    surge_pct: float = 3.0        # 급등 기준 (%)
    drop_pct: float = -3.0        # 급락 기준 (%)
    volume_spike_ratio: float = 3.0  # 거래량 급증 배수
    stop_loss: float = 0.0        # 손절 가격
    take_profit: float = 0.0      # 익절 가격
    trailing_stop_pct: float = 0.0   # 트레일링 스탑 (%, 0이면 미사용)

    # 트레일링 스탑용 고점 추적
    highest_price: float = 0.0


# 기본 임계값 (AI 미설정 시 폴백)
DEFAULT_THRESHOLDS = StockThresholds()


class EventDetector:
    """
    실시간 가격 데이터에서 이벤트 감지 — 종목별 임계값 기반

    AI Agent가 종목 선정 시 set_thresholds()로 종목별 기준을 설정하고,
    WebSocket 체결 데이터가 들어올 때마다 해당 기준으로 이벤트를 감지한다.
    """

    def __init__(self):
        # 종목별 임계값 (AI가 설정)
        self._thresholds: dict[str, StockThresholds] = {}

        # 실시간 데이터 캐시
        self._prev_prices: dict[str, float] = {}
        self._volume_history: dict[str, list[int]] = defaultdict(list)

        # 이벤트 중복 발행 방지 (종목별 마지막 이벤트 타입+시간)
        self._last_events: dict[str, tuple[str, float]] = {}
        self.EVENT_DEDUP_SEC = 60  # 같은 이벤트 60초 내 재발행 방지

    def set_thresholds(self, symbol: str, **kwargs) -> None:
        """종목별 감시 임계값 설정 (AI Agent가 호출)

        사용 예:
            event_detector.set_thresholds("005930",
                surge_pct=2.0, drop_pct=-2.0,
                volume_spike_ratio=2.5,
                stop_loss=71000, take_profit=76000,
                trailing_stop_pct=2.0,
            )
        """
        import math

        # 값 검증: NaN, None, 숫자가 아닌 값 필터링
        validated = {}
        for k, v in kwargs.items():
            if isinstance(v, (int, float)) and not math.isnan(v):
                validated[k] = v
            else:
                logger.warning("유효하지 않은 임계값 무시: {} {} = {}", symbol, k, v)

        if symbol in self._thresholds:
            th = self._thresholds[symbol]
            for k, v in validated.items():
                if hasattr(th, k):
                    setattr(th, k, v)
        else:
            self._thresholds[symbol] = StockThresholds(**validated)

        # trailing_stop 설정 시 highest_price를 stop_loss 기반으로 초기화
        th = self._thresholds[symbol]
        if 0 < th.trailing_stop_pct < 100 and th.highest_price == 0 and th.stop_loss > 0:
            # stop_loss = highest × (1 - pct/100) → highest = stop_loss / (1 - pct/100)
            th.highest_price = th.stop_loss / (1 - th.trailing_stop_pct / 100)

        logger.debug("임계값 설정: {} → {}", symbol, self._thresholds[symbol])

    def get_thresholds(self, symbol: str) -> StockThresholds:
        return self._thresholds.get(symbol, DEFAULT_THRESHOLDS)

    def set_stop_loss(self, symbol: str, price: float) -> None:
        self.set_thresholds(symbol, stop_loss=price)

    def set_take_profit(self, symbol: str, price: float) -> None:
        self.set_thresholds(symbol, take_profit=price)

    def remove_levels(self, symbol: str) -> None:
        self._thresholds.pop(symbol, None)

    def clear_all(self) -> None:
        """전체 초기화 (장 시작 시)"""
        self._thresholds.clear()
        self._prev_prices.clear()
        self._volume_history.clear()
        self._last_events.clear()

    @property
    def monitored_symbols(self) -> list[str]:
        return list(self._thresholds.keys())

    async def on_price_update(self, data: dict) -> None:
        """실시간 가격 업데이트 처리 + 이벤트 감지"""
        symbol = data.get("symbol", "")
        price = data.get("price", 0)
        volume = data.get("volume", 0)
        change_rate = data.get("change_rate", 0)

        if not symbol or price <= 0:
            return

        th = self.get_thresholds(symbol)

        # 가격 업데이트 이벤트 발행
        await event_bus.publish(Event(
            type=EventType.PRICE_UPDATE,
            data=data,
            source="event_detector",
        ))

        # 트레일링 스탑 고점 갱신
        if th.trailing_stop_pct > 0 and price > th.highest_price:
            th.highest_price = price
            # 트레일링 스탑 가격 = 고점 × (1 - trailing_pct/100)
            new_stop = price * (1 - th.trailing_stop_pct / 100)
            if new_stop > th.stop_loss:
                th.stop_loss = new_stop
                logger.debug("트레일링 스탑 상향: {} → 손절 {:,.0f}원 (고점 {:,.0f})",
                             symbol, new_stop, price)

        # 거래량 급증 감지
        await self._check_volume_spike(symbol, volume, th, data)

        # 급등/급락 감지
        await self._check_price_movement(symbol, price, change_rate, th, data)

        # 손절/익절 감지
        await self._check_stop_take(symbol, price, th, data)

        # 캐시 업데이트
        self._prev_prices[symbol] = price
        self._volume_history[symbol].append(volume)
        if len(self._volume_history[symbol]) > 20:
            self._volume_history[symbol] = self._volume_history[symbol][-20:]

    async def _check_volume_spike(
        self, symbol: str, volume: int, th: StockThresholds, data: dict,
    ) -> None:
        history = self._volume_history.get(symbol, [])
        if len(history) < 5:
            return

        avg_volume = sum(history[-10:]) / len(history[-10:])
        if avg_volume > 0 and volume > avg_volume * th.volume_spike_ratio:
            if not self._should_dedup(symbol, "VOLUME_SPIKE"):
                spike_ratio = volume / avg_volume
                logger.info("거래량 급증: {} ({:.1f}배, 기준 {:.1f}배)",
                            symbol, spike_ratio, th.volume_spike_ratio)
                await event_bus.publish(Event(
                    type=EventType.VOLUME_SPIKE,
                    data={**data, "avg_volume": avg_volume, "spike_ratio": spike_ratio},
                    source="event_detector",
                ))

    async def _check_price_movement(
        self, symbol: str, price: float, change_rate: float,
        th: StockThresholds, data: dict,
    ) -> None:
        if change_rate >= th.surge_pct:
            if not self._should_dedup(symbol, "PRICE_SURGE"):
                logger.info("급등: {} ({:+.2f}%, 기준 {:.1f}%)",
                            symbol, change_rate, th.surge_pct)
                await event_bus.publish(Event(
                    type=EventType.PRICE_SURGE,
                    data=data,
                    source="event_detector",
                ))
        elif change_rate <= th.drop_pct:
            if not self._should_dedup(symbol, "PRICE_DROP"):
                logger.info("급락: {} ({:+.2f}%, 기준 {:.1f}%)",
                            symbol, change_rate, th.drop_pct)
                await event_bus.publish(Event(
                    type=EventType.PRICE_DROP,
                    data=data,
                    source="event_detector",
                ))

    async def _check_stop_take(
        self, symbol: str, price: float, th: StockThresholds, data: dict,
    ) -> None:
        if th.stop_loss > 0 and price <= th.stop_loss:
            if not self._should_dedup(symbol, "STOP_LOSS"):
                logger.warning("손절선 도달: {} (현재 {:,.0f}, 손절 {:,.0f})",
                               symbol, price, th.stop_loss)
                await event_bus.publish(Event(
                    type=EventType.STOP_LOSS_HIT,
                    data={**data, "stop_loss_price": th.stop_loss},
                    source="event_detector",
                ))

        if th.take_profit > 0 and price >= th.take_profit:
            if not self._should_dedup(symbol, "TAKE_PROFIT"):
                logger.info("익절선 도달: {} (현재 {:,.0f}, 익절 {:,.0f})",
                            symbol, price, th.take_profit)
                await event_bus.publish(Event(
                    type=EventType.TAKE_PROFIT_HIT,
                    data={**data, "take_profit_price": th.take_profit},
                    source="event_detector",
                ))

    def _should_dedup(self, symbol: str, event_type: str) -> bool:
        """같은 종목+이벤트 중복 발행 방지"""
        import time
        key = f"{symbol}:{event_type}"
        now = time.time()
        last = self._last_events.get(key)
        if last and now - last[1] < self.EVENT_DEDUP_SEC:
            return True
        self._last_events[key] = (event_type, now)
        return False


event_detector = EventDetector()
