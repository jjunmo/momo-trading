"""KIS WebSocket 실시간 시세 스트리밍"""
import asyncio
import json
from typing import Callable, Coroutine, Any

import websockets
from loguru import logger

from core.config import settings


def _ws_is_closed(ws) -> bool:
    """websockets 버전 호환 연결 상태 확인 (13+ 에서 .closed 제거됨)"""
    if ws is None:
        return True
    if hasattr(ws, "closed"):
        return ws.closed
    # websockets 14+ ClientConnection: close_code가 None이면 아직 연결 중
    return ws.close_code is not None


class KISWebSocket:
    """한국투자증권 WebSocket 실시간 시세 클라이언트"""

    def __init__(self):
        self._ws_domestic = None
        self._ws_overseas = None
        self._running = False
        self._subscriptions: set[str] = set()
        self._on_price_callback: Callable[[dict], Coroutine[Any, Any, None]] | None = None
        self._reconnect_delay = 5
        self._approval_key: str | None = None

    def set_on_price(self, callback: Callable[[dict], Coroutine[Any, Any, None]]) -> None:
        self._on_price_callback = callback

    async def connect(self) -> None:
        """WebSocket 연결 시작"""
        self._running = True
        await self._get_approval_key()
        logger.debug("KIS WebSocket 연결 시작")

    async def disconnect(self) -> None:
        """WebSocket 연결 종료"""
        self._running = False
        for ws in (self._ws_domestic, self._ws_overseas):
            if ws:
                await ws.close()
        self._ws_domestic = None
        self._ws_overseas = None
        self._subscriptions.clear()
        logger.debug("KIS WebSocket 연결 종료")

    async def _get_approval_key(self) -> None:
        """WebSocket 접속 키 발급"""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://openapi.koreainvestment.com:9443/oauth2/Approval",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": settings.KIS_PAPER_APP_KEY if settings.is_paper_trading else settings.KIS_APP_KEY,
                        "secretkey": settings.KIS_PAPER_APP_SECRET if settings.is_paper_trading else settings.KIS_APP_SECRET,
                    },
                )
                if resp.status_code == 200:
                    self._approval_key = resp.json().get("approval_key")
                    logger.debug("WebSocket approval key 발급 완료")
        except Exception as e:
            logger.warning("WebSocket approval key 발급 실패: {}", str(e))

    async def subscribe(self, symbol: str, market: str = "KRX") -> bool:
        """종목 실시간 시세 구독"""
        if len(self._subscriptions) >= 41:
            logger.warning("WebSocket 구독 한도 초과 (최대 41종목)")
            return False

        key = f"{market}:{symbol}"
        if key in self._subscriptions:
            return True

        ws = await self._get_ws(market)
        if not ws:
            return False

        try:
            sub_msg = self._build_subscribe_msg(symbol, market)
            await ws.send(json.dumps(sub_msg))
            self._subscriptions.add(key)
            logger.debug("종목 구독: {}", key)
            return True
        except Exception as e:
            logger.error("구독 실패: {} - {}", key, str(e))
            return False

    async def unsubscribe(self, symbol: str, market: str = "KRX") -> None:
        """종목 구독 해제"""
        key = f"{market}:{symbol}"
        if key not in self._subscriptions:
            return

        ws = await self._get_ws(market)
        if ws:
            try:
                unsub_msg = self._build_unsubscribe_msg(symbol, market)
                await ws.send(json.dumps(unsub_msg))
            except Exception:
                pass
        self._subscriptions.discard(key)
        logger.debug("종목 구독 해제: {}", key)

    async def _get_ws(self, market: str):
        """시장별 WebSocket 연결 반환 (없으면 생성)"""
        if market in ("KOSPI", "KOSDAQ", "KRX", "NXT"):
            if not self._ws_domestic or _ws_is_closed(self._ws_domestic):
                try:
                    self._ws_domestic = await websockets.connect(
                        settings.KIS_WS_URL_DOMESTIC
                    )
                except Exception as e:
                    logger.error("국내 WebSocket 연결 실패: {}", str(e))
                    return None
            return self._ws_domestic
        else:
            if not self._ws_overseas or _ws_is_closed(self._ws_overseas):
                try:
                    self._ws_overseas = await websockets.connect(
                        settings.KIS_WS_URL_OVERSEAS
                    )
                except Exception as e:
                    logger.error("해외 WebSocket 연결 실패: {}", str(e))
                    return None
            return self._ws_overseas

    def _build_subscribe_msg(self, symbol: str, market: str) -> dict:
        return {
            "header": {
                "approval_key": self._approval_key or "",
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": self._resolve_tr_id(market),
                    "tr_key": symbol,
                }
            },
        }

    @staticmethod
    def _resolve_tr_id(market: str) -> str:
        """시장별 실시간 체결가 tr_id"""
        if market in ("KOSPI", "KOSDAQ", "KRX", "NXT"):
            # 통합(H0UNCNT0): KRX+NXT 어디서든 체결 수신 (모의투자 미지원)
            if settings.is_paper_trading:
                return "H0STCNT0"  # 모의투자는 KRX만
            return "H0UNCNT0"
        return "HDFSCNT0"

    def _build_unsubscribe_msg(self, symbol: str, market: str) -> dict:
        msg = self._build_subscribe_msg(symbol, market)
        msg["header"]["tr_type"] = "2"
        return msg

    async def listen(self) -> None:
        """WebSocket 메시지 수신 루프"""
        while self._running:
            tasks = []
            if self._ws_domestic and not _ws_is_closed(self._ws_domestic):
                tasks.append(asyncio.create_task(self._listen_ws(self._ws_domestic, "domestic")))
            if self._ws_overseas and not _ws_is_closed(self._ws_overseas):
                tasks.append(asyncio.create_task(self._listen_ws(self._ws_overseas, "overseas")))

            if not tasks:
                await asyncio.sleep(1)
                continue

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

    async def _listen_ws(self, ws, ws_type: str) -> None:
        """개별 WebSocket 메시지 수신"""
        try:
            async for raw_msg in ws:
                if not self._running:
                    break
                await self._handle_message(raw_msg, ws_type, ws)
        except websockets.ConnectionClosed:
            logger.warning("{} WebSocket 연결 끊김, 재연결 시도...", ws_type)
            if self._running:
                await asyncio.sleep(self._reconnect_delay)
        except Exception as e:
            logger.error("{} WebSocket 오류: {}", ws_type, str(e))

    async def _handle_message(self, raw_msg: str, ws_type: str, ws) -> None:
        """수신된 메시지 처리"""
        # PINGPONG 응답
        if raw_msg == "PINGPONG":
            await ws.send("PINGPONG")
            return

        try:
            # KIS WebSocket은 '|' 구분자로 데이터를 보냄
            if "|" in raw_msg:
                parts = raw_msg.split("|")
                if len(parts) >= 4:
                    tr_id = parts[1]
                    data_count = int(parts[2])
                    data_str = parts[3]
                    price_data = self._parse_price_data(tr_id, data_str)
                    if price_data and self._on_price_callback:
                        await self._on_price_callback(price_data)
            else:
                # JSON 형태 응답 (구독 확인 등)
                data = json.loads(raw_msg)
                header = data.get("header", {})
                if header.get("tr_id") == "PINGPONG":
                    await ws.send(raw_msg)
        except Exception as e:
            logger.debug("메시지 파싱 오류 (무시): {}", str(e))

    def _parse_price_data(self, tr_id: str, data_str: str) -> dict | None:
        """체결 데이터 파싱"""
        fields = data_str.split("^")
        if not fields:
            return None

        if tr_id in ("H0STCNT0", "H0NXCNT0", "H0UNCNT0"):  # 국내 실시간 체결 (KRX/NXT/통합)
            if len(fields) < 20:
                return None
            return {
                "market": "KRX",  # 통합(H0UNCNT0)은 KRX/NXT 구분 없이 수신
                "symbol": fields[0],
                "time": fields[1],
                "price": float(fields[2]) if fields[2] else 0,
                "change": float(fields[4]) if fields[4] else 0,
                "change_rate": float(fields[5]) if fields[5] else 0,
                "volume": int(fields[12]) if fields[12] else 0,
                "cumulative_volume": int(fields[13]) if fields[13] else 0,
            }
        elif tr_id in ("HDFSCNT0",):  # 해외 실시간 체결
            if len(fields) < 10:
                return None
            return {
                "market": "OVERSEAS",
                "symbol": fields[0],
                "time": fields[1],
                "price": float(fields[2]) if fields[2] else 0,
                "change": float(fields[6]) if fields[6] else 0,
                "change_rate": float(fields[7]) if fields[7] else 0,
                "volume": int(fields[8]) if fields[8] else 0,
                "cumulative_volume": int(fields[9]) if fields[9] else 0,
            }
        return None

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    @property
    def is_connected(self) -> bool:
        domestic_ok = self._ws_domestic and not _ws_is_closed(self._ws_domestic)
        overseas_ok = self._ws_overseas and not _ws_is_closed(self._ws_overseas)
        return bool(domestic_ok or overseas_ok)


# 싱글톤
kis_websocket = KISWebSocket()
