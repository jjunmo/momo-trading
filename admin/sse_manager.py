"""SSE (Server-Sent Events) 관리자 - 실시간 브라우저 푸시"""
import asyncio
import json
from uuid import uuid4

from loguru import logger


class SSEManager:
    """모든 연결된 클라이언트에 SSE 이벤트를 브로드캐스트"""

    def __init__(self):
        self._connections: dict[str, asyncio.Queue] = {}

    def connect(self) -> tuple[str, asyncio.Queue]:
        """새 클라이언트 연결 → (client_id, queue) 반환"""
        client_id = str(uuid4())[:8]
        queue: asyncio.Queue = asyncio.Queue()
        self._connections[client_id] = queue
        logger.debug("SSE 클라이언트 연결: {} (총 {}명)", client_id, len(self._connections))
        return client_id, queue

    def disconnect(self, client_id: str) -> None:
        """클라이언트 연결 해제"""
        self._connections.pop(client_id, None)
        logger.debug("SSE 클라이언트 해제: {} (총 {}명)", client_id, len(self._connections))

    async def broadcast(self, data: dict) -> None:
        """모든 클라이언트에 이벤트 전송"""
        if not self._connections:
            return
        message = json.dumps(data, ensure_ascii=False, default=str)
        dead = []
        for client_id, queue in self._connections.items():
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(client_id)
        for cid in dead:
            self._connections.pop(cid, None)

    @property
    def client_count(self) -> int:
        return len(self._connections)


sse_manager = SSEManager()
