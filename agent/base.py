"""Agent 기본 인터페이스"""
from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """모든 Agent의 기본 클래스"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 식별자"""

    async def start(self) -> None:
        """Agent 시작 (필요 시 오버라이드)"""

    async def stop(self) -> None:
        """Agent 종료 (필요 시 오버라이드)"""
