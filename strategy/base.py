"""TradingStrategy Protocol"""
from typing import Protocol, runtime_checkable

from strategy.signal import TradeSignal


@runtime_checkable
class TradingStrategy(Protocol):
    """매매 전략 프로토콜"""

    @property
    def strategy_type(self) -> str: ...

    async def evaluate(self, analysis: dict) -> TradeSignal | None:
        """분석 결과를 바탕으로 매매 시그널 생성"""
        ...
