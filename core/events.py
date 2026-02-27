"""내부 이벤트 버스 (asyncio 기반) - 가격변동/시그널 발행"""
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine

from loguru import logger


class EventType(str, Enum):
    """이벤트 유형"""
    # 시세 관련
    PRICE_UPDATE = "PRICE_UPDATE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    PRICE_SURGE = "PRICE_SURGE"
    PRICE_DROP = "PRICE_DROP"

    # 기술지표 관련
    INDICATOR_SIGNAL = "INDICATOR_SIGNAL"

    # 포트폴리오 관련
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"

    # Agent 관련
    AGENT_CYCLE_START = "AGENT_CYCLE_START"
    AGENT_CYCLE_END = "AGENT_CYCLE_END"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    ORDER_EXECUTED = "ORDER_EXECUTED"
    RECOMMENDATION_CREATED = "RECOMMENDATION_CREATED"

    # 시스템 관련
    MARKET_OPEN = "MARKET_OPEN"
    MARKET_CLOSE = "MARKET_CLOSE"
    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass
class Event:
    """이벤트 데이터"""
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = ""


EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """asyncio 기반 이벤트 버스"""

    def __init__(self):
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)
        logger.debug("이벤트 핸들러 등록: {} → {}", event_type.value, handler.__name__)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_events())
        logger.info("이벤트 버스 시작")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("이벤트 버스 중지")

    async def _process_events(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                handlers = self._handlers.get(event.type, [])
                for handler in handlers:
                    try:
                        await handler(event)
                    except Exception as e:
                        logger.error(
                            "이벤트 핸들러 오류: {} - {} - {}",
                            event.type.value, handler.__name__, str(e)
                        )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break


# 싱글톤 이벤트 버스
event_bus = EventBus()
