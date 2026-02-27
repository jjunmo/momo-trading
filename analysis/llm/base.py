"""LLM Provider Protocol - Tier 공통 인터페이스"""
from typing import Protocol, runtime_checkable

from trading.enums import LLMProvider, LLMTier


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """LLM 제공자 공통 인터페이스"""

    @property
    def provider(self) -> LLMProvider: ...

    @property
    def tier(self) -> LLMTier: ...

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """텍스트 생성"""
        ...

    async def is_available(self) -> bool:
        """사용 가능 여부 확인"""
        ...
