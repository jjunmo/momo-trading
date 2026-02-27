"""LLM Factory — Claude Code CLI 전용"""
import asyncio
import time

from loguru import logger

from analysis.llm.claude_code_provider import ClaudeCodeProvider
from core.config import settings
from trading.enums import LLMTier


class LLMFactory:
    """Claude Code CLI 기반 LLM 라우팅

    - Tier 1 (빠름): 스캔, 선별, 기술분석 해석
    - Tier 2 (프리미엄): 최종 검토, 매매 결정
    """

    def __init__(self):
        self._providers = {
            LLMTier.TIER1: ClaudeCodeProvider(LLMTier.TIER1),
            LLMTier.TIER2: ClaudeCodeProvider(LLMTier.TIER2),
        }

    async def generate(
        self, prompt: str, tier: LLMTier = LLMTier.TIER1, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
    ) -> tuple[str, str]:
        """텍스트 생성 (최대 2회 시도)

        Returns:
            (생성 텍스트, 사용된 provider 이름)
        """
        provider = self._providers[tier]

        if not await provider.is_available():
            raise RuntimeError("Claude Code CLI를 찾을 수 없습니다 (PATH 확인)")

        last_error = None
        for attempt in range(2):
            try:
                start = time.time()
                result = await provider.generate(prompt, system_prompt)
                elapsed_ms = int((time.time() - start) * 1000)
                provider_name = provider.provider.value
                model_id = provider.model_id

                logger.debug(
                    "LLM 생성 완료: {} / {} ({}ms)",
                    provider_name, model_id, elapsed_ms,
                )

                # LLM 대화 내역 자동 로깅 (모니터링용)
                await self._log_llm_conversation(
                    tier=tier,
                    provider=provider_name,
                    model=model_id,
                    system_prompt=system_prompt,
                    prompt=prompt,
                    response=result,
                    elapsed_ms=elapsed_ms,
                    symbol=symbol,
                    cycle_id=cycle_id,
                )

                return result, provider_name
            except Exception as e:
                last_error = e
                if attempt == 0:
                    logger.warning("LLM 호출 실패, 재시도: {}", str(e)[:100])
                    await asyncio.sleep(2)

        raise last_error

    async def _log_llm_conversation(
        self, *, tier: LLMTier, provider: str, model: str,
        system_prompt: str, prompt: str, response: str, elapsed_ms: int,
        symbol: str | None = None, cycle_id: str | None = None,
    ) -> None:
        """LLM 프롬프트/응답을 activity log에 기록"""
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                "LLM_CALL", "COMPLETE",
                f"[{tier.value}] {provider} ({model}) — {elapsed_ms/1000:.1f}초",
                detail={
                    "llm_system_prompt": system_prompt[:2000] if system_prompt else "",
                    "llm_prompt": prompt[:5000],
                    "llm_response": response[:5000],
                    "llm_model": model,
                },
                llm_provider=provider,
                llm_tier=tier.value,
                execution_time_ms=elapsed_ms,
                symbol=symbol,
                cycle_id=cycle_id,
            )
        except Exception as e:
            logger.debug("LLM 대화 로깅 실패 (무시): {}", str(e))

    async def generate_tier1(
        self, prompt: str, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
    ) -> tuple[str, str]:
        """Tier 1 (빠른 분석용)"""
        return await self.generate(prompt, LLMTier.TIER1, system_prompt, symbol=symbol, cycle_id=cycle_id)

    async def generate_tier2(
        self, prompt: str, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
    ) -> tuple[str, str]:
        """Tier 2 (프리미엄 분석용)"""
        return await self.generate(prompt, LLMTier.TIER2, system_prompt, symbol=symbol, cycle_id=cycle_id)

    def get_llm_status(self) -> dict:
        """현재 LLM 설정 상태 반환 (Admin API용)"""
        tier1_model = settings.CLAUDE_CODE_MODEL_TIER1 or settings.CLAUDE_CODE_MODEL or "haiku"
        tier2_model = settings.CLAUDE_CODE_MODEL_TIER2 or settings.CLAUDE_CODE_MODEL or "sonnet"
        return {
            "tier1": {"provider": "CLAUDE_CODE", "model": tier1_model},
            "tier2": {"provider": "CLAUDE_CODE", "model": tier2_model},
            "available_providers": [
                {
                    "id": "CLAUDE_CODE",
                    "name": "Claude Code (로컬)",
                    "models": {"tier1": tier1_model, "tier2": tier2_model},
                    "has_key": True,
                },
            ],
        }


llm_factory = LLMFactory()
