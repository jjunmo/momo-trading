"""LLM Factory — Claude Code / Codex CLI 라우팅 + 장애 감지."""
import asyncio
import time
from typing import Any

from loguru import logger

from analysis.llm.base import LLMProviderProtocol
from analysis.llm.claude_code_provider import ClaudeCodeProvider
from analysis.llm.codex_cli_provider import CodexCLIProvider
from core.config import settings
from trading.enums import ActivityPhase, ActivityType, LLMProvider, LLMTier


ProviderClass = type[ClaudeCodeProvider] | type[CodexCLIProvider]
ProviderInstance = LLMProviderProtocol


class LLMFactory:
    """LLM provider 라우팅 + 세션 lifecycle 관리.

    - Tier 1: 스캔/선별/빠른 분석
    - Tier 2: 최종 검토/매매 판단
    - 선택 provider가 실패하면 반대 provider로 자동 폴백 (양방향 대칭)
    """

    _provider_classes: dict[LLMProvider, ProviderClass] = {
        LLMProvider.CLAUDE_CODE: ClaudeCodeProvider,
        LLMProvider.CODEX_CLI: CodexCLIProvider,
    }

    def __init__(self):
        self._selected_provider = settings.llm_provider
        self._fallback_provider = self._pick_fallback(self._selected_provider)
        self._providers = self._build_providers(self._selected_provider)
        self._fallback_providers = (
            self._build_providers(self._fallback_provider)
            if self._fallback_provider is not None
            else {}
        )

        self._failure_count: int = 0
        self._last_error: str = ""
        self._last_failure_at: float = 0.0
        self._alert_sent: bool = False

    @staticmethod
    def _pick_fallback(selected: LLMProvider) -> LLMProvider | None:
        """선택된 provider의 반대편을 폴백으로. 매핑에 없으면 None."""
        for candidate in LLMProvider:
            if candidate != selected:
                return candidate
        return None

    def _build_providers(self, provider: LLMProvider) -> dict[LLMTier, ProviderInstance]:
        provider_cls = self._provider_classes[provider]
        return {
            LLMTier.TIER1: provider_cls(LLMTier.TIER1),
            LLMTier.TIER2: provider_cls(LLMTier.TIER2),
        }

    @property
    def selected_provider(self) -> LLMProvider:
        return self._selected_provider

    def _selected_provider_cls(self) -> ProviderClass:
        return self._provider_classes[self._selected_provider]

    async def generate(
        self, prompt: str, tier: LLMTier = LLMTier.TIER1, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
    ) -> tuple[str, str]:
        """텍스트 생성.

        Returns:
            (생성 텍스트, 사용된 provider 이름)
        """
        try:
            result = await self._generate_with_provider(
                provider_name=self._selected_provider,
                providers=self._providers,
                prompt=prompt,
                tier=tier,
                system_prompt=system_prompt,
                symbol=symbol,
                cycle_id=cycle_id,
            )
            self._on_success()
            return result
        except Exception as primary_error:
            primary_summary = f"{type(primary_error).__name__}: {str(primary_error)[:200]}"
            logger.warning("{} 1차 provider 실패: {}", self._selected_provider.value, primary_summary)

            if self._fallback_providers and self._fallback_provider is not None:
                fallback_name = self._fallback_provider
                try:
                    logger.warning(
                        "{} 실패 → {} 폴백 시도",
                        self._selected_provider.value, fallback_name.value,
                    )
                    result = await self._generate_with_provider(
                        provider_name=fallback_name,
                        providers=self._fallback_providers,
                        prompt=prompt,
                        tier=tier,
                        system_prompt=system_prompt,
                        symbol=symbol,
                        cycle_id=cycle_id,
                    )
                    self._on_success()
                    return result
                except Exception as fallback_error:
                    fallback_summary = f"{type(fallback_error).__name__}: {str(fallback_error)[:200]}"
                    error_summary = (
                        f"{self._selected_provider.value}: {primary_summary} | "
                        f"{fallback_name.value}: {fallback_summary}"
                    )
                    await self._on_failure(error_summary)
                    raise RuntimeError(f"LLM provider 연결 불가: {error_summary}") from fallback_error

            await self._on_failure(primary_summary)
            raise RuntimeError(f"LLM provider 연결 불가: {primary_summary}") from primary_error

    async def _generate_with_provider(
        self, *, provider_name: LLMProvider, providers: dict[LLMTier, ProviderInstance],
        prompt: str, tier: LLMTier, system_prompt: str,
        symbol: str | None, cycle_id: str | None,
    ) -> tuple[str, str]:
        provider = providers[tier]
        provider_cls = self._provider_classes[provider_name]

        if not await provider.is_available():
            raise RuntimeError(f"{provider_name.value} CLI를 찾을 수 없습니다")

        errors: list[str] = []
        attempts = [
            ("세션", None),
            ("새세션", "rotate"),
            ("stateless", "reset"),
        ]

        for label, session_action in attempts:
            try:
                if session_action == "rotate":
                    provider_cls._rotate_session(reason="1차 실패 후 재시도")
                    await asyncio.sleep(1)
                elif session_action == "reset":
                    provider_cls.reset_session()
                    await asyncio.sleep(2)

                return await self._call_provider(
                    provider, prompt, system_prompt, tier, symbol, cycle_id,
                )
            except Exception as e:
                err_detail = f"{type(e).__name__}: {str(e)[:80] or 'no details'}"
                errors.append(f"{label}: {err_detail}")
                logger.warning("{} {} 실패: {}", provider_name.value, label, err_detail)

        raise RuntimeError(f"{provider_name.value} 연결 불가: {' | '.join(errors)}")

    async def _call_provider(
        self, provider: ProviderInstance, prompt: str, system_prompt: str,
        tier: LLMTier, symbol: str | None, cycle_id: str | None,
    ) -> tuple[str, str]:
        start = time.time()
        result = await provider.generate(prompt, system_prompt)
        elapsed_ms = int((time.time() - start) * 1000)
        provider_name = provider.provider.value
        model_id = provider.model_id

        logger.debug(
            "LLM 생성 완료: {} / {} ({}ms)",
            provider_name, model_id, elapsed_ms,
        )

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

    def _on_success(self) -> None:
        if self._failure_count > 0:
            logger.info(
                "LLM provider 연결 복구 (이전 연속 실패 {}회)", self._failure_count,
            )
            asyncio.ensure_future(self._send_recovery_alert())
        self._failure_count = 0
        self._last_error = ""
        self._alert_sent = False

    async def _on_failure(self, error_summary: str) -> None:
        self._failure_count += 1
        self._last_error = error_summary
        self._last_failure_at = time.time()

        if not self._alert_sent or self._failure_count % 5 == 0:
            await self._send_failure_alert(error_summary)
            self._alert_sent = True

    async def _send_failure_alert(self, error_summary: str) -> None:
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.LLM_CALL, ActivityPhase.ERROR,
                f"LLM provider 연결 불가 — 매매 분석 중단 "
                f"(연속 {self._failure_count}회 실패)\n"
                f"원인: {error_summary[:200]}\n"
                f"조치: 선택 provider CLI 상태 확인 후 서버 재시작 또는 인증 상태 확인 필요",
            )
        except Exception as e:
            logger.error("장애 알림 전송 실패: {}", str(e))

    async def _send_recovery_alert(self) -> None:
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.LLM_CALL, ActivityPhase.COMPLETE,
                "LLM provider 연결 복구 — 매매 분석 재개",
            )
        except Exception:
            pass

    @staticmethod
    def _detect_llm_purpose(system_prompt: str, prompt: str, symbol: str | None) -> str:
        import re
        sp = system_prompt[:200].lower() if system_prompt else ""
        if "리스크" in sp or "한도" in sp:
            return "리스크 한도"
        if "스크리너" in sp or "스크리" in sp or "종목을 선별" in sp:
            return "시장 스캔"
        if "재평가" in sp or "hold/sell" in sp or "보유종목" in sp:
            return "보유 재평가"
        if prompt:
            match = re.search(r"종목 분석 요청:\s*(.+?)\s*\(", prompt[:200])
            if match:
                return f"{match.group(1)} 분석"
        if symbol:
            return f"{symbol} 분석"
        return "분석"

    async def _log_llm_conversation(
        self, *, tier: LLMTier, provider: str, model: str,
        system_prompt: str, prompt: str, response: str, elapsed_ms: int,
        symbol: str | None = None, cycle_id: str | None = None,
    ) -> None:
        purpose = self._detect_llm_purpose(system_prompt, prompt, symbol)
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.LLM_CALL, ActivityPhase.COMPLETE,
                f"[{tier.value}] {purpose} — {provider} ({model}) — {elapsed_ms/1000:.1f}초",
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
        return await self.generate(prompt, LLMTier.TIER1, system_prompt, symbol=symbol, cycle_id=cycle_id)

    async def generate_tier2(
        self, prompt: str, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
    ) -> tuple[str, str]:
        return await self.generate(prompt, LLMTier.TIER2, system_prompt, symbol=symbol, cycle_id=cycle_id)

    def start_session(self) -> str | None:
        return self._selected_provider_cls().start_session()

    def pause_session(self) -> str | None:
        return self._selected_provider_cls().pause_session()

    def resume_session(self, session_id: str) -> None:
        self._selected_provider_cls().resume_session(session_id)

    def end_session(self) -> str | None:
        return self._selected_provider_cls().end_session()

    def get_session_id(self) -> str | None:
        return self._selected_provider_cls().get_session_id()

    def get_llm_usage(self) -> dict[str, Any]:
        providers = {
            provider.value: provider_cls.get_usage_snapshot()
            for provider, provider_cls in self._provider_classes.items()
        }
        return {
            "selected_provider": self._selected_provider.value,
            "app_usage": providers.get(self._selected_provider.value, {}),
            "providers": providers,
        }

    def get_llm_status(self) -> dict:
        provider_cls = self._selected_provider_cls()
        session_id = provider_cls.get_session_id()
        is_expired = provider_cls._is_session_expired()

        available_providers = []
        for provider in LLMProvider:
            path = settings.get_llm_cli_path(provider)
            available_providers.append({
                "id": provider.value,
                "name": "Claude Code (로컬)" if provider == LLMProvider.CLAUDE_CODE else "Codex CLI (로컬)",
                "models": {
                    "tier1": settings.get_llm_model(provider, LLMTier.TIER1),
                    "tier2": settings.get_llm_model(provider, LLMTier.TIER2),
                },
                "reasoning_effort": {
                    "tier1": settings.get_llm_reasoning_effort(provider, LLMTier.TIER1),
                    "tier2": settings.get_llm_reasoning_effort(provider, LLMTier.TIER2),
                },
                "cli_path": path,
                "available": bool(path),
                "mcp_disabled": (
                    settings.CODEX_DISABLE_MCP
                    if provider == LLMProvider.CODEX_CLI
                    else None
                ),
            })

        return {
            "selected_provider": self._selected_provider.value,
            "tier1": {
                "provider": self._selected_provider.value,
                "model": settings.get_llm_model(self._selected_provider, LLMTier.TIER1),
                "reasoning_effort": settings.get_llm_reasoning_effort(self._selected_provider, LLMTier.TIER1),
            },
            "tier2": {
                "provider": self._selected_provider.value,
                "model": settings.get_llm_model(self._selected_provider, LLMTier.TIER2),
                "reasoning_effort": settings.get_llm_reasoning_effort(self._selected_provider, LLMTier.TIER2),
            },
            "orchestrator": {
                "provider": self._selected_provider.value,
                "model": settings.get_orchestrator_llm_model_for_provider(self._selected_provider),
                "reasoning_effort": settings.get_orchestrator_llm_reasoning_effort_for_provider(self._selected_provider),
            },
            "health": {
                "available": self._failure_count == 0,
                "failure_count": self._failure_count,
                "last_error": self._last_error[:200] if self._last_error else None,
                "last_failure_at": self._last_failure_at or None,
            },
            "session": {
                "id": session_id,
                "initialized": provider_cls._session_initialized,
                "expired": is_expired,
            },
            "available_providers": available_providers,
        }


llm_factory = LLMFactory()
