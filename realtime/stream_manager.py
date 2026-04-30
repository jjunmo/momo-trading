"""WebSocket 연결 관리 - 동적 구독/해제, 재연결"""
import asyncio

from loguru import logger

from trading.kis_websocket import kis_websocket


class StreamManager:
    """
    WebSocket 스트림 관리자
    - KIS 제한: 세션당 41종목
    - AI 선정 종목만 동적 구독/해제
    - 끊김 시 자동 재연결
    """

    def __init__(self):
        self._priority_symbols: dict[str, str] = {}  # symbol -> market
        self._running = False
        self._listen_task: asyncio.Task | None = None

    async def start(self) -> None:
        """스트림 관리 시작"""
        self._running = True
        try:
            await kis_websocket.connect()
            self._listen_task = asyncio.create_task(self._run_listener())
            logger.debug("스트림 매니저 시작")
        except Exception as e:
            logger.warning("WebSocket 연결 실패 (나중에 재시도): {}", str(e))

    async def stop(self) -> None:
        """스트림 관리 중지"""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        await kis_websocket.disconnect()
        logger.debug("스트림 매니저 중지")

    async def subscribe_symbols(self, symbols: list[tuple[str, str]]) -> None:
        """종목 리스트 구독 (symbol, market) 쌍"""
        for symbol, market in symbols:
            if kis_websocket.subscription_count >= 41:
                logger.warning("구독 한도 도달 (41종목), 우선순위 낮은 종목 해제 필요")
                break
            success = await kis_websocket.subscribe(symbol, market)
            if success:
                self._priority_symbols[symbol] = market

    async def unsubscribe_symbols(self, symbols: list[str]) -> None:
        """종목 구독 해제"""
        for symbol in symbols:
            market = self._priority_symbols.pop(symbol, "KRX")
            await kis_websocket.unsubscribe(symbol, market)

    async def update_subscriptions(self, new_symbols: list[tuple[str, str]]) -> None:
        """AI가 선정한 새 종목으로 구독 목록 업데이트"""
        new_set = {s[0] for s in new_symbols}
        current_set = set(self._priority_symbols.keys())

        # 해제할 종목
        to_remove = current_set - new_set
        if to_remove:
            await self.unsubscribe_symbols(list(to_remove))

        # 추가할 종목
        to_add = [(s, m) for s, m in new_symbols if s not in current_set]
        if to_add:
            await self.subscribe_symbols(to_add)

    async def _run_listener(self) -> None:
        """WebSocket 수신 루프 (재연결 포함)"""
        while self._running:
            try:
                await kis_websocket.listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WebSocket 리스너 오류: {}", str(e))
                if self._running:
                    logger.debug("5초 후 재연결 시도...")
                    await asyncio.sleep(5)
                    try:
                        await kis_websocket.connect()
                        # 기존 구독 복원
                        for symbol, market in self._priority_symbols.items():
                            await kis_websocket.subscribe(symbol, market)

                        # WS gap 보호: 재연결 직후 보유종목 가격 폴링 1회
                        # 5초 gap 동안 손절선 통과한 케이스 즉시 보정
                        await self._poll_after_reconnect()
                    except Exception as re:
                        logger.error("재연결 실패: {}", str(re))

    async def _poll_after_reconnect(self) -> None:
        """재연결 직후 보유종목 현재가 1회 폴링 → 손절/익절 임계값 즉시 체크.

        WS 재연결 5초 gap 동안 손절선 통과 케이스 누락 방지.
        """
        try:
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client
            from realtime.event_detector import event_detector

            holdings = await account_manager.get_holdings()
            if not holdings:
                return

            logger.info("WS 재연결 gap 보호: 보유 {}종목 현재가 폴링", len(holdings))

            for h in holdings:
                try:
                    resp = await mcp_client.get_current_price(h.symbol)
                    if not (resp.success and resp.data):
                        continue
                    price = float(resp.data.get("price", resp.data.get("current_price", 0)) or 0)
                    if price > 0:
                        # event_detector가 손절·익절 체크 + 발동
                        await event_detector.on_price_update({"symbol": h.symbol, "price": price})
                except Exception as e:
                    logger.debug("[{}] 폴링 실패: {}", h.symbol, str(e))
        except Exception as e:
            logger.warning("재연결 후 폴링 처리 실패: {}", str(e))

    @property
    def subscription_count(self) -> int:
        return kis_websocket.subscription_count

    @property
    def is_connected(self) -> bool:
        return kis_websocket.is_connected


stream_manager = StreamManager()
