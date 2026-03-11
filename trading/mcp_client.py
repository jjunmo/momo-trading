"""KIS Trading MCP 서버 HTTP/SSE 클라이언트"""
import asyncio
import json
import time
from typing import Any

import httpx
from loguru import logger

from core.config import settings
from trading.models import MCPResponse

# SSE 재연결 설정
_SSE_RECONNECT_DELAY = 2.0  # 재연결 대기 초
_SSE_MAX_RECONNECT_DELAY = 30.0  # 최대 재연결 대기 초
_SSE_MAX_RECONNECT_ATTEMPTS = 50  # 최대 재연결 시도 횟수

# KIS API rate limit: 모의투자 초당 ~10건 (공식 20건이지만 실제 더 엄격)
_RATE_LIMIT_PER_SEC = 8
_RATE_LIMIT_WINDOW = 1.0  # 초
_MAX_CONCURRENT_CALLS = 3  # 동시 MCP 호출 상한


class MCPClient:
    """KIS Trading MCP Docker 서버와 통신하는 SSE 클라이언트

    MCP SSE 프로토콜:
    1. GET /sse → 영구 SSE 스트림 (서버→클라이언트 메시지 수신)
    2. POST /messages/?session_id=xxx → 도구 호출 요청 (202 Accepted)
    3. 결과는 SSE 스트림의 'message' 이벤트로 수신

    SSE 연결이 끊기면 자동 재연결 + 대기 중인 요청을 즉시 실패 처리합니다.
    """

    def __init__(self):
        self._base_url = settings.KIS_MCP_URL.rstrip("/sse").rstrip("/")
        self._post_client: httpx.AsyncClient | None = None
        self._sse_client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._sse_task: asyncio.Task | None = None
        self._shutting_down = False
        self._reconnect_count = 0
        # Rate limiter: 초당 요청 타임스탬프 + 동시 호출 세마포어
        self._call_timestamps: list[float] = []
        self._rate_lock = asyncio.Lock()
        self._call_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)

    @property
    def is_connected(self) -> bool:
        return self._post_client is not None and self._session_id is not None

    async def connect(self) -> None:
        self._shutting_down = False
        self._reconnect_count = 0

        # POST 전용 클라이언트 (도구 호출용) — 30초 타임아웃
        self._post_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"Host": "localhost:3000"},
        )
        # SSE 전용 클라이언트 — read timeout 없음 (장기 스트림)
        self._sse_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(None, connect=10.0),
            follow_redirects=True,
            headers={"Host": "localhost:3000"},
        )
        try:
            await self._start_sse()
            logger.info("MCP 서버 연결 성공: {}", self._base_url)
        except Exception as e:
            logger.error("MCP 서버 연결 실패: {}", str(e))
            raise

    async def disconnect(self) -> None:
        self._shutting_down = True
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sse_task = None
        for client in (self._post_client, self._sse_client):
            if client:
                await client.aclose()
        self._post_client = None
        self._sse_client = None
        self._fail_all_pending("MCP 연결 종료")
        self._session_id = None
        logger.info("MCP 서버 연결 종료")

    def _fail_all_pending(self, reason: str) -> None:
        """대기 중인 모든 Future를 즉시 실패 처리"""
        if not self._pending:
            return
        count = len(self._pending)
        for msg_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result({"error": {"message": reason}})
        self._pending.clear()
        if count:
            logger.warning("SSE 끊김 → 대기 요청 {}건 즉시 실패 처리: {}", count, reason)

    async def _start_sse(self) -> None:
        """SSE 연결 시작 — 세션 ID 획득 → 프로토콜 초기화 → 백그라운드 리스너"""
        ready = asyncio.Event()
        self._sse_task = asyncio.create_task(self._sse_loop(ready))
        # 세션 ID 획득까지 대기 (최대 10초)
        try:
            await asyncio.wait_for(ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("MCP SSE 세션 초기화 타임아웃")
            raise ConnectionError("MCP SSE 세션 초기화 타임아웃")

        # MCP 프로토콜 초기화 핸드셰이크
        await self._mcp_initialize()

    async def _mcp_initialize(self) -> None:
        """MCP 프로토콜 초기화 핸드셰이크"""
        # 1. initialize 요청
        init_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[init_id] = fut

        await self._post_client.post(self._session_id, json={
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "momo-trading", "version": "0.1.0"},
            },
        })

        try:
            await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(init_id, None)
            logger.warning("MCP initialize 응답 타임아웃")
            return

        # 2. initialized 알림 (응답 불필요)
        await self._post_client.post(self._session_id, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        logger.info("MCP 프로토콜 초기화 완료")

    async def _sse_loop(self, initial_ready: asyncio.Event) -> None:
        """SSE 연결 유지 루프 — 끊기면 자동 재연결

        흐름:
        1. SSE 스트림을 백그라운드 task로 시작
        2. ready 이벤트 대기 (세션 ID 수신)
        3. 초기화 후 스트림이 끊길 때까지 대기
        4. 끊기면 대기 요청 실패 처리 → 재연결
        """
        delay = _SSE_RECONNECT_DELAY
        is_first = True

        while not self._shutting_down:
            ready = initial_ready if is_first else asyncio.Event()

            if not is_first:
                # 재연결: SSE 클라이언트 재생성
                if self._sse_client:
                    try:
                        await self._sse_client.aclose()
                    except Exception:
                        pass
                self._sse_client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=httpx.Timeout(None, connect=10.0),
                    follow_redirects=True,
                    headers={"Host": "localhost:3000"},
                )

            # SSE 리스너를 백그라운드 task로 시작
            listen_task = asyncio.create_task(self._sse_listen_once(ready))

            if not is_first:
                # 재연결: 세션 ID 획득 대기 → 프로토콜 초기화
                try:
                    await asyncio.wait_for(ready.wait(), timeout=10.0)
                    await self._mcp_initialize()
                    self._reconnect_count = 0
                    delay = _SSE_RECONNECT_DELAY
                    logger.info("MCP SSE 재연결 성공 (세션: {})",
                                self._session_id[:20] if self._session_id else "?")
                except Exception as e:
                    logger.warning("MCP SSE 재연결 실패: {}", str(e))
                    listen_task.cancel()
                    try:
                        await listen_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    self._fail_all_pending("SSE 재연결 실패")
                    self._session_id = None
                    self._reconnect_count += 1
                    if self._reconnect_count > _SSE_MAX_RECONNECT_ATTEMPTS:
                        logger.error("MCP SSE 재연결 한도 초과")
                        break
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, _SSE_MAX_RECONNECT_DELAY)
                    continue

            # 스트림이 끊길 때까지 대기
            try:
                await listen_task
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MCP SSE 스트림 종료: {}", str(e))

            if self._shutting_down:
                break

            # 끊김 → 대기 중인 요청 즉시 실패 처리
            self._fail_all_pending("SSE 연결 끊김, 재연결 중")
            self._session_id = None

            self._reconnect_count += 1
            if self._reconnect_count > _SSE_MAX_RECONNECT_ATTEMPTS:
                logger.error("MCP SSE 재연결 한도 초과 ({}회)", _SSE_MAX_RECONNECT_ATTEMPTS)
                break

            logger.info("MCP SSE 재연결 시도 ({}/{}) — {:.1f}초 대기",
                        self._reconnect_count, _SSE_MAX_RECONNECT_ATTEMPTS, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _SSE_MAX_RECONNECT_DELAY)
            is_first = False

    async def _sse_listen_once(self, ready: asyncio.Event) -> None:
        """단일 SSE 연결 수신 — 연결이 끊기면 반환"""
        async with self._sse_client.stream("GET", "/sse") as response:
            event_type = ""
            data_buffer = ""
            async for line in response.aiter_lines():
                if self._shutting_down:
                    return
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_buffer = line[5:].strip()
                elif line == "" and data_buffer:
                    await self._handle_sse_event(event_type, data_buffer, ready)
                    event_type = ""
                    data_buffer = ""
                elif line.startswith(":"):
                    pass

    async def _handle_sse_event(
        self, event_type: str, data: str, ready: asyncio.Event
    ) -> None:
        """SSE 이벤트 처리"""
        if event_type == "endpoint" or "/messages" in data:
            # 세션 초기화 — data에 메시지 엔드포인트 경로
            if "/messages" in data:
                self._session_id = data
                logger.debug("MCP 세션 초기화 완료: {}", self._session_id)
                ready.set()
                return

        if event_type == "message":
            try:
                msg = json.loads(data)
                msg_id = msg.get("id")
                logger.debug("MCP SSE 메시지 수신: id={}, pending={}", msg_id, list(self._pending.keys()))
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                elif msg_id is not None:
                    logger.warning("MCP SSE 메시지 id={}에 대한 대기 Future 없음", msg_id)
            except json.JSONDecodeError:
                logger.warning("MCP SSE 메시지 파싱 실패: {}", data[:200])
        else:
            # 알 수 없는 이벤트 타입 로깅
            if event_type and event_type != "endpoint":
                logger.debug("MCP SSE 이벤트: type={}, data={}", event_type, data[:100])

    async def _wait_for_session(self, timeout: float = 5.0) -> bool:
        """SSE 세션이 준비될 때까지 대기 (재연결 중일 수 있으므로)"""
        if self._session_id:
            return True
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self._session_id:
                return True
            if self._shutting_down:
                return False
            await asyncio.sleep(0.2)
        return self._session_id is not None

    async def _rate_limit(self) -> None:
        """KIS API 초당 요청 한도 준수 — 초과 시 대기"""
        async with self._rate_lock:
            now = time.monotonic()
            # 1초 이전 타임스탬프 제거
            self._call_timestamps = [
                t for t in self._call_timestamps
                if now - t < _RATE_LIMIT_WINDOW
            ]
            if len(self._call_timestamps) >= _RATE_LIMIT_PER_SEC:
                # 가장 오래된 요청이 1초 지날 때까지 대기
                wait = _RATE_LIMIT_WINDOW - (now - self._call_timestamps[0]) + 0.05
                if wait > 0:
                    logger.debug("KIS rate limit 대기: {:.2f}초", wait)
                    await asyncio.sleep(wait)
            self._call_timestamps.append(time.monotonic())

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None,
        _retry: int = 0,
    ) -> MCPResponse:
        """MCP 도구 호출 — 세마포어 + rate limit으로 초당 한도 준수"""
        if not self._post_client:
            return MCPResponse(success=False, error="MCP 클라이언트가 연결되지 않았습니다")

        # SSE 재연결 중이면 잠시 대기
        if not self._session_id:
            if not await self._wait_for_session(timeout=10.0):
                return MCPResponse(success=False, error="MCP SSE 세션 없음 (재연결 실패)")

        # 동시 호출 제한 + rate limit
        async with self._call_semaphore:
            await self._rate_limit()
            return await self._call_tool_inner(tool_name, arguments, _retry)

    async def _call_tool_inner(
        self, tool_name: str, arguments: dict[str, Any] | None,
        _retry: int,
    ) -> MCPResponse:
        """실제 MCP 도구 호출 (세마포어 내부)"""
        msg_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut

        try:
            response = await self._post_client.post(self._session_id, json=payload)
            if response.status_code not in (200, 202):
                self._pending.pop(msg_id, None)
                return MCPResponse(
                    success=False, error=f"HTTP {response.status_code}"
                )

            # SSE 스트림에서 결과 대기 (최대 30초)
            result = await asyncio.wait_for(fut, timeout=30.0)

            if "error" in result:
                error_msg = result["error"].get("message", "MCP 도구 호출 실패")
                return await self._maybe_retry_rate_limit(
                    error_msg, tool_name, arguments, _retry)

            content = result.get("result", {}).get("content", [])
            is_error = result.get("result", {}).get("isError", False)
            data = {}
            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if is_error:
                        return await self._maybe_retry_rate_limit(
                            text[:300], tool_name, arguments, _retry)
                    try:
                        data = json.loads(text)
                    except (json.JSONDecodeError, KeyError):
                        data = {"text": text}
                    break

            if not data:
                logger.warning("MCP 도구 응답 content 비어있음: {}", str(result)[:300])
                return MCPResponse(success=False, error=f"빈 응답: {tool_name}", data={})

            return MCPResponse(success=True, data=data)

        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            logger.error("MCP 도구 호출 타임아웃: {}", tool_name)
            return MCPResponse(success=False, error=f"타임아웃: {tool_name}")
        except httpx.ConnectError:
            self._pending.pop(msg_id, None)
            logger.error("MCP 서버 연결 불가: {}", self._base_url)
            return MCPResponse(success=False, error="MCP 서버 연결 불가")
        except Exception as e:
            self._pending.pop(msg_id, None)
            logger.error("MCP 도구 호출 오류 ({}): {}", tool_name, str(e))
            return MCPResponse(success=False, error=str(e))

    async def _maybe_retry_rate_limit(
        self, error_msg: str, tool_name: str,
        arguments: dict[str, Any] | None, _retry: int,
    ) -> MCPResponse:
        """rate limit 에러면 1초 대기 후 재시도, 아니면 그대로 실패"""
        if "초당 거래건수" in error_msg and _retry < 2:
            wait = 1.0 + _retry * 0.5
            logger.warning("KIS rate limit ({}) → {:.1f}초 대기 후 재시도 ({}/2)",
                           tool_name, wait, _retry + 1)
            await asyncio.sleep(wait)
            await self._rate_limit()
            return await self._call_tool_inner(tool_name, arguments, _retry + 1)
        return MCPResponse(success=False, error=error_msg[:200])

    async def list_tools(self) -> list[dict]:
        """사용 가능한 MCP 도구 목록 조회"""
        if not self._post_client or not self._session_id:
            return []

        msg_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/list",
        }

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut

        try:
            await self._post_client.post(self._session_id, json=payload)
            result = await asyncio.wait_for(fut, timeout=10.0)
            return result.get("result", {}).get("tools", [])
        except Exception as e:
            self._pending.pop(msg_id, None)
            logger.error("MCP 도구 목록 조회 실패: {}", str(e))
            return []

    # === KIS 값 변환 헬퍼 (KIS API는 모든 숫자를 문자열로 반환) ===

    @staticmethod
    def _to_float(val, default: float = 0.0) -> float:
        """KIS 문자열 값 → float 변환 ("72300" → 72300.0, "" → 0.0)"""
        if val is None or val == "":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_int(val, default: int = 0) -> int:
        """KIS 문자열 값 → int 변환"""
        if val is None or val == "":
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    # === 편의 메서드: KIS MCP 도구 래퍼 ===

    async def get_current_price(self, symbol: str, market: str = "KRX") -> MCPResponse:
        """현재가 조회 (KIS 원본 키 → 정규화)"""
        if market in ("KOSPI", "KOSDAQ", "KRX"):
            resp = await self.call_tool("inquery-stock-price", {"symbol": symbol})
        else:
            resp = await self.call_tool("inquery-overseas-stock-price", {
                "symbol": symbol, "exchange": market
            })
        if resp.success and resp.data:
            d = resp.data
            # KIS는 모든 값을 문자열로 반환 → float 변환 후 or 체인 (0.0은 falsy)
            price_val = (self._to_float(d.get("stck_prpr"))
                         or self._to_float(d.get("last"))
                         or self._to_float(d.get("price")))
            resp.data = {
                **d,
                "price": price_val,
                "current_price": price_val,
                "change": self._to_float(d.get("prdy_vrss")),
                "change_rate": self._to_float(d.get("prdy_ctrt")),
                "volume": self._to_int(d.get("acml_vol")),
                "per": d.get("per", "N/A"),
                "pbr": d.get("pbr", "N/A"),
            }
        return resp

    async def get_account_balance(self) -> MCPResponse:
        """계좌 잔고 조회"""
        return await self.call_tool("inquery-balance")

    async def get_daily_price(
        self, symbol: str, period: str = "D", count: int = 30, market: str = "KRX"
    ) -> MCPResponse:
        """일봉 데이터 조회 (KIS 원본 키 → 정규화)"""
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")

        if market in ("KOSPI", "KOSDAQ", "KRX"):
            resp = await self.call_tool("inquery-stock-info", {
                "symbol": symbol, "start_date": start_date, "end_date": end_date,
            })
        else:
            resp = await self.call_tool("inquery-overseas-stock-price", {
                "symbol": symbol, "exchange": market,
            })

        # KIS 원본 응답 정규화: output2[] → prices[]
        # KIS API는 일봉 배열을 output2에 반환 (output1은 요약 헤더)
        if resp.success and resp.data:
            raw_items = (resp.data.get("output2")
                         or resp.data.get("output")
                         or resp.data.get("prices")
                         or [])
            if isinstance(raw_items, list) and raw_items:
                prices = []
                for item in raw_items:
                    prices.append({
                        "date": item.get("stck_bsop_date", ""),
                        "open": self._to_float(item.get("stck_oprc")),
                        "high": self._to_float(item.get("stck_hgpr")),
                        "low": self._to_float(item.get("stck_lwpr")),
                        "close": self._to_float(item.get("stck_clpr")),
                        "volume": self._to_int(item.get("acml_vol")),
                        "change": self._to_float(item.get("prdy_vrss")),
                        "change_rate": self._to_float(item.get("prdy_ctrt")),
                    })
                resp.data["prices"] = prices
            else:
                logger.warning("[{}] 일봉 데이터 키 누락 — keys: {}", symbol,
                               list(resp.data.keys())[:10])
        return resp

    async def place_order(
        self, symbol: str, side: str, quantity: int,
        price: float | None = None, market: str = "KRX"
    ) -> MCPResponse:
        """주문 실행 (KIS 원본 키 → 정규화)"""
        order_type = "buy" if side == "BUY" else "sell"
        if market in ("KOSPI", "KOSDAQ", "KRX"):
            resp = await self.call_tool("order-stock", {
                "symbol": symbol,
                "quantity": quantity,
                "price": int(price) if price else 0,
                "order_type": order_type,
            })
        else:
            resp = await self.call_tool("order-overseas-stock", {
                "symbol": symbol, "exchange": market,
                "quantity": quantity,
                "price": price or 0,
                "order_type": order_type,
            })
        if resp.success and resp.data:
            d = resp.data
            # KIS rt_cd='1' → 주문 실패 (잔고 부족 등)
            if d.get("rt_cd") == "1":
                error_msg = d.get("msg1", "KIS 주문 실패")
                logger.warning("KIS 주문 거부: {}", error_msg)
                resp.success = False
                resp.error = error_msg
                return resp
            # KIS 주문번호: 최상위 또는 output 중첩, 대소문자 혼용
            output = d.get("output", {}) if isinstance(d.get("output"), dict) else {}
            order_id = (
                d.get("ODNO") or d.get("odno")
                or output.get("ODNO") or output.get("odno")
                or d.get("order_id", "")
            )
            if not order_id:
                logger.warning("주문 응답에서 주문번호 미발견, 원본: {}", str(d)[:500])
            resp.data = {
                **d,
                "order_id": order_id,
                "filled_quantity": self._to_int(
                    d.get("exec_qty") or output.get("exec_qty")
                    or d.get("filled_quantity")
                ),
                "filled_price": self._to_float(
                    d.get("exec_prc") or output.get("exec_prc")
                    or d.get("filled_price")
                ),
            }
        return resp

    async def get_volume_rank(self, market: str = "KRX") -> MCPResponse:
        """거래량 상위 종목 조회 (KIS API 직접 호출)"""
        from trading.kis_api import get_volume_rank

        market_code = "J" if market in ("KOSPI", "KOSDAQ", "KRX") else market
        try:
            result = await get_volume_rank(market=market_code)
            return MCPResponse(success=result.get("success", False), data=result)
        except Exception as e:
            logger.error("거래량순위 조회 실패: {}", str(e))
            return MCPResponse(success=False, error=str(e))

    async def get_minute_price(
        self, symbol: str, period: str = "5", market: str = "KRX"
    ) -> MCPResponse:
        """분봉 데이터 조회 (KIS API 직접 호출)"""
        from trading.kis_api import get_minute_chart

        try:
            result = await get_minute_chart(symbol, period)
            return MCPResponse(success=result.get("success", False), data=result)
        except Exception as e:
            logger.error("분봉 조회 실패 ({}): {}", symbol, str(e))
            return MCPResponse(success=False, error=str(e))

    async def get_fluctuation_rank(self, market: str = "KRX", sort: str = "top") -> MCPResponse:
        """등락률 상위/하위 종목 조회 (KIS API 직접 호출)"""
        from trading.kis_api import get_fluctuation_rank

        market_code = "J" if market in ("KOSPI", "KOSDAQ", "KRX") else market
        try:
            result = await get_fluctuation_rank(sort=sort, market=market_code)
            return MCPResponse(success=result.get("success", False), data=result)
        except Exception as e:
            logger.error("등락률순위 조회 실패: {}", str(e))
            return MCPResponse(success=False, error=str(e))

    async def get_stock_ask(self, symbol: str) -> MCPResponse:
        """호가 조회"""
        return await self.call_tool("inquery-stock-ask", {"symbol": symbol})

    async def get_order_list(self) -> MCPResponse:
        """주문 체결 내역 조회 (당일 미체결)"""
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        return await self.call_tool("inquery-order-list", {
            "start_date": today,
            "end_date": today,
        })


# 싱글톤 MCP 클라이언트
mcp_client = MCPClient()
