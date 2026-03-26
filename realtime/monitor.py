"""실시간 모니터 - WebSocket으로 시세 수신 + 이벤트 감지 + 폴링 폴백"""
import asyncio
import time

from loguru import logger

from realtime.event_detector import event_detector
from realtime.stream_manager import stream_manager
from trading.kis_websocket import kis_websocket


class RealtimeMonitor:
    """
    실시간 시세 모니터
    - WebSocket에서 가격 데이터 수신
    - EventDetector로 이벤트 감지
    - Agent 트리거 연결
    - WebSocket 단절 시 폴링 폴백 (보유종목 현재가 API 조회)
    """

    # WebSocket 데이터 수신 없이 이 시간(초) 경과 시 단절로 판정
    WS_STALE_THRESHOLD_SEC = 60
    # 폴링 간격 (초)
    POLL_INTERVAL_SEC = 300  # 5분

    def __init__(self):
        self._running = False
        self._last_ws_data_time: float = 0.0  # monotonic
        self._polling_active = False
        self._health_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        """실시간 모니터링 시작"""
        self._running = True
        self._last_ws_data_time = time.monotonic()
        kis_websocket.set_on_price(self._on_price_update)
        await stream_manager.start()
        # WebSocket 상태 점검 루프 시작
        self._health_task = asyncio.create_task(self._ws_health_loop())
        logger.debug("실시간 모니터 시작 (폴링 폴백 대기)")

    async def stop(self) -> None:
        """실시간 모니터링 중지"""
        self._running = False
        self._polling_active = False
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        await stream_manager.stop()
        logger.debug("실시간 모니터 중지")

    async def _on_price_update(self, data: dict) -> None:
        """WebSocket에서 가격 데이터 수신 시 호출"""
        self._last_ws_data_time = time.monotonic()
        # WebSocket 복구 → 폴링 비활성화
        if self._polling_active:
            self._polling_active = False
            if self._poll_task and not self._poll_task.done():
                self._poll_task.cancel()
                self._poll_task = None
            logger.debug("WebSocket 데이터 수신 복구 → 폴링 폴백 비활성화")
        await event_detector.on_price_update(data)

    async def _ws_health_loop(self) -> None:
        """30초마다 WebSocket 상태 점검 → 필요 시 폴링 활성화"""
        while self._running:
            try:
                await asyncio.sleep(30)
                if not self._running:
                    break

                # 장외 시간: 폴링 불필요 (WebSocket 끊김은 정상)
                from scheduler.market_calendar import market_calendar
                if not market_calendar.is_krx_trading_hours():
                    if self._polling_active:
                        self._polling_active = False
                        if self._poll_task and not self._poll_task.done():
                            self._poll_task.cancel()
                            self._poll_task = None
                        logger.debug("장외 시간 → 폴링 폴백 비활성화")
                    continue

                ws_disconnected = not stream_manager.is_connected
                data_stale = (time.monotonic() - self._last_ws_data_time) > self.WS_STALE_THRESHOLD_SEC

                if (ws_disconnected or data_stale) and not self._polling_active:
                    reason = "연결 끊김" if ws_disconnected else f"데이터 {self.WS_STALE_THRESHOLD_SEC}초 미수신"
                    logger.warning("WebSocket 단절 감지 ({}) → 폴링 폴백 활성화", reason)
                    self._polling_active = True
                    # 폴링 태스크 시작
                    if self._poll_task is None or self._poll_task.done():
                        self._poll_task = asyncio.create_task(self._poll_loop())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WebSocket 상태 점검 오류: {}", str(e))

    async def _poll_loop(self) -> None:
        """폴링 루프: 보유종목 현재가를 주기적으로 API 조회 → event_detector에 주입"""
        logger.debug("폴링 폴백 루프 시작 ({}초 간격)", self.POLL_INTERVAL_SEC)
        while self._running and self._polling_active:
            try:
                await self._poll_holdings_prices()
                await asyncio.sleep(self.POLL_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("폴링 루프 오류: {}", str(e))
                await asyncio.sleep(30)  # 오류 시 짧게 대기 후 재시도
        logger.debug("폴링 폴백 루프 종료")

    async def _poll_holdings_prices(self) -> None:
        """보유종목 현재가 MCP 조회 → event_detector.on_price_update() 전달"""
        from trading.account_manager import account_manager
        from trading.mcp_client import mcp_client

        try:
            holdings = await account_manager.get_holdings()
            if not holdings:
                return

            polled_count = 0
            for h in holdings:
                if not h.symbol or h.quantity <= 0:
                    continue
                try:
                    resp = await mcp_client.get_current_price(h.symbol)
                    if not resp.success or not resp.data:
                        continue
                    price = float(resp.data.get("price", 0))
                    if price <= 0:
                        continue

                    # event_detector에 주입 (기존 손절/익절 로직 재사용)
                    await event_detector.on_price_update({
                        "symbol": h.symbol,
                        "price": price,
                        "volume": 0,
                        "change_rate": 0,
                        "source": "polling_fallback",
                    })
                    polled_count += 1
                except Exception as e:
                    logger.debug("폴링 현재가 조회 실패 {}: {}", h.symbol, str(e))

            if polled_count > 0:
                logger.debug("폴링 폴백: {}종목 현재가 조회 완료", polled_count)

        except Exception as e:
            logger.error("폴링 보유종목 조회 오류: {}", str(e))

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return stream_manager.is_connected

    @property
    def is_polling(self) -> bool:
        """폴링 폴백이 활성화되어 있는지"""
        return self._polling_active


realtime_monitor = RealtimeMonitor()
