"""KIS API 공유 초당 요청 limiter.

KIS의 초당 거래건수 초과(EGW00201)는 **앱키 단위 합산** 한도다.
직접 REST 호출(`trading/kis_api.py`)과 MCP 경유 호출(`trading/mcp_client.py`)이
같은 앱키를 쓰므로, 두 경로가 **하나의 limiter**를 거쳐야 합산 한도를 지킨다.
(둘이 각자 따로 제한하면 합쳐졌을 때 한도를 넘어 EGW00201이 발생)

슬라이딩 윈도우 방식: 최근 1초 내 요청 수가 한도에 도달하면 가장 오래된 요청이
1초를 넘길 때까지 대기. lock을 잡은 채 대기하므로 호출들이 자연히 직렬화·페이싱된다.
"""
import asyncio
import time

# KIS 실효 한도: 모의투자 초당 ~10건(공식 20이나 실제 더 엄격). 두 경로 합산 안전 마진.
_RATE_LIMIT_PER_SEC = 8
_RATE_LIMIT_WINDOW = 1.0  # 초


class KisRateLimiter:
    def __init__(self, per_sec: int = _RATE_LIMIT_PER_SEC, window: float = _RATE_LIMIT_WINDOW):
        self._per_sec = per_sec
        self._window = window
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """요청 직전 호출 — 초당 한도 초과 시 대기."""
        async with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < self._window]
            if len(self._timestamps) >= self._per_sec:
                wait = self._window - (now - self._timestamps[0]) + 0.05
                if wait > 0:
                    await asyncio.sleep(wait)
            self._timestamps.append(time.monotonic())


# 프로세스 전역 단일 인스턴스 — 모든 KIS 호출 경로가 공유
kis_rate_limiter = KisRateLimiter()
