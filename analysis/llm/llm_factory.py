"""LLM Factory — 백엔드 라우팅 (anthropic API | claude_code CLI)"""
import asyncio
import time

from loguru import logger

from analysis.llm.anthropic_provider import AnthropicProvider
from analysis.llm.claude_code_provider import ClaudeCodeProvider
from core.config import settings
from trading.enums import ActivityPhase, ActivityType, LLMTier


class LLMFactory:
    """백엔드 라우팅 + 장애 복구

    - LLM_BACKEND=anthropic: AsyncAnthropic SDK. SDK 내장 max_retries만. tier downgrade 없음.
    - LLM_BACKEND=claude_code: CLI subprocess. 기존 3단 세션 폴백 유지.
    """

    def __init__(self):
        # 백엔드별 Tier1/Tier2 provider 준비
        self._anthropic_providers = {
            LLMTier.TIER1: AnthropicProvider(LLMTier.TIER1),
            LLMTier.TIER2: AnthropicProvider(LLMTier.TIER2),
        }
        self._claude_code_providers = {
            LLMTier.TIER1: ClaudeCodeProvider(LLMTier.TIER1),
            LLMTier.TIER2: ClaudeCodeProvider(LLMTier.TIER2),
        }
        # 장애 상태 추적
        self._failure_count: int = 0
        self._last_error: str = ""
        self._last_failure_at: float = 0.0
        self._alert_sent: bool = False

    def _active_providers(self) -> dict:
        return (
            self._anthropic_providers
            if settings.LLM_BACKEND == "anthropic"
            else self._claude_code_providers
        )

    async def generate(
        self, prompt: str, tier: LLMTier = LLMTier.TIER1, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
        market_context: str = "", stock_baseline: str = "",
    ) -> tuple[str, str]:
        """텍스트 생성 — 백엔드별 분기.

        Layered caching 파라미터(`market_context`, `stock_baseline`)는
        anthropic backend에서만 사용. claude_code backend는 무시.

        Returns:
            (생성 텍스트, 사용된 provider 이름)
        """
        if settings.LLM_BACKEND == "anthropic":
            return await self._generate_anthropic(
                prompt, tier, system_prompt, symbol, cycle_id,
                market_context, stock_baseline,
            )
        else:
            return await self._generate_claude_code(
                prompt, tier, system_prompt, symbol, cycle_id,
            )

    # ── anthropic 백엔드 ──

    async def _generate_anthropic(
        self, prompt: str, tier: LLMTier, system_prompt: str,
        symbol: str | None, cycle_id: str | None,
        market_context: str, stock_baseline: str,
    ) -> tuple[str, str]:
        """Anthropic API 호출 — SDK 내장 재시도만. 실패 시 raise + activity_log."""
        provider = self._anthropic_providers[tier]
        if not await provider.is_available():
            raise RuntimeError("ANTHROPIC_API_KEY 미설정")

        start = time.time()
        try:
            result = await provider.generate(
                prompt, system_prompt,
                market_context=market_context,
                stock_baseline=stock_baseline,
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            err_detail = f"{type(e).__name__}: {str(e)[:120] or 'no details'}"
            logger.error("Anthropic API 실패 ({}ms): {}", elapsed_ms, err_detail)
            await self._on_failure(err_detail)
            raise
        elapsed_ms = int((time.time() - start) * 1000)
        provider_name = provider.provider.value
        model_id = provider.model_id

        logger.debug("LLM 생성 완료: {} / {} ({}ms)", provider_name, model_id, elapsed_ms)
        await self._log_llm_conversation(
            tier=tier, provider=provider_name, model=model_id,
            system_prompt=system_prompt,
            prompt=self._compose_log_prompt(market_context, stock_baseline, prompt),
            response=result, elapsed_ms=elapsed_ms,
            symbol=symbol, cycle_id=cycle_id,
        )
        self._on_success()
        return result, provider_name

    @staticmethod
    def _compose_log_prompt(market_ctx: str, baseline: str, fresh: str) -> str:
        """activity_log 기록용 — 3층을 구분선과 함께 합친 문자열"""
        parts = []
        if market_ctx:
            parts.append(f"[L2a market_context]\n{market_ctx}")
        if baseline:
            parts.append(f"[L2b stock_baseline]\n{baseline}")
        parts.append(f"[L3 fresh]\n{fresh}")
        return "\n\n---\n\n".join(parts)

    # ── claude_code 백엔드 (기존 3단 폴백 유지) ──

    async def _generate_claude_code(
        self, prompt: str, tier: LLMTier, system_prompt: str,
        symbol: str | None, cycle_id: str | None,
    ) -> tuple[str, str]:
        """Claude Code CLI 호출 — 세션 rotate 3단 폴백."""
        provider = self._claude_code_providers[tier]
        if not await provider.is_available():
            raise RuntimeError("Claude Code CLI를 찾을 수 없습니다 (PATH 확인)")

        errors: list[str] = []

        # Step 1: 현재 세션
        try:
            result = await self._call_claude_code(
                provider, prompt, system_prompt, tier, symbol, cycle_id,
            )
            self._on_success()
            return result
        except Exception as e:
            err_detail = f"{type(e).__name__}: {str(e)[:80] or 'no details'}"
            errors.append(f"세션: {err_detail}")
            logger.warning("Claude 1차(세션) 실패: {}", err_detail)

        # Step 2: 새 세션
        try:
            if ClaudeCodeProvider.get_session_id():
                ClaudeCodeProvider._rotate_session(reason="1차 실패 후 재시도")
            await asyncio.sleep(1)
            result = await self._call_claude_code(
                provider, prompt, system_prompt, tier, symbol, cycle_id,
            )
            self._on_success()
            return result
        except Exception as e:
            err_detail = f"{type(e).__name__}: {str(e)[:80] or 'no details'}"
            errors.append(f"새세션: {err_detail}")
            logger.warning("Claude 2차(새세션) 실패: {}", err_detail)

        # Step 3: stateless
        try:
            if ClaudeCodeProvider.get_session_id():
                ClaudeCodeProvider.reset_session()
            await asyncio.sleep(2)
            result = await self._call_claude_code(
                provider, prompt, system_prompt, tier, symbol, cycle_id,
            )
            self._on_success()
            return result
        except Exception as e:
            err_detail = f"{type(e).__name__}: {str(e)[:80] or 'no details'}"
            errors.append(f"stateless: {err_detail}")
            logger.error("Claude 3차(stateless) 실패: {}", err_detail)

        error_summary = " | ".join(errors)
        await self._on_failure(error_summary)
        raise RuntimeError(f"Claude Code 연결 불가: {error_summary}")

    async def _call_claude_code(
        self, provider: ClaudeCodeProvider, prompt: str, system_prompt: str,
        tier: LLMTier, symbol: str | None, cycle_id: str | None,
    ) -> tuple[str, str]:
        start = time.time()
        result = await provider.generate(prompt, system_prompt)
        elapsed_ms = int((time.time() - start) * 1000)
        provider_name = provider.provider.value
        model_id = provider.model_id

        logger.debug("LLM 생성 완료: {} / {} ({}ms)", provider_name, model_id, elapsed_ms)
        await self._log_llm_conversation(
            tier=tier, provider=provider_name, model=model_id,
            system_prompt=system_prompt, prompt=prompt, response=result,
            elapsed_ms=elapsed_ms, symbol=symbol, cycle_id=cycle_id,
        )
        return result, provider_name

    def _on_success(self) -> None:
        """호출 성공 시 장애 상태 복구"""
        if self._failure_count > 0:
            logger.info(
                "Claude Code 연결 복구 (이전 연속 실패 {}회)", self._failure_count,
            )
            # 복구 알림
            asyncio.ensure_future(self._send_recovery_alert())
        self._failure_count = 0
        self._last_error = ""
        self._alert_sent = False

    async def _on_failure(self, error_summary: str) -> None:
        """모든 시도 실패 시 장애 상태 기록 + 알림"""
        self._failure_count += 1
        self._last_error = error_summary
        self._last_failure_at = time.time()

        # 연속 실패 시 알림 (중복 방지: 첫 실패 + 이후 5회마다)
        if not self._alert_sent or self._failure_count % 5 == 0:
            await self._send_failure_alert(error_summary)
            self._alert_sent = True

    async def _send_failure_alert(self, error_summary: str) -> None:
        """장애 알림 — activity_logger + SSE"""
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.LLM_CALL, ActivityPhase.ERROR,
                f"\u26a0\ufe0f Claude Code 연결 불가 — 매매 분석 중단 "
                f"(연속 {self._failure_count}회 실패)\n"
                f"원인: {error_summary[:200]}\n"
                f"조치: Claude Code 상태 확인 후 서버 재시작 또는 토큰 충전 필요",
            )
        except Exception as e:
            logger.error("장애 알림 전송 실패: {}", str(e))

    async def _send_recovery_alert(self) -> None:
        """복구 알림"""
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.LLM_CALL, ActivityPhase.COMPLETE,
                f"\u2705 Claude Code 연결 복구 — 매매 분석 재개",
            )
        except Exception:
            pass

    @staticmethod
    def _detect_llm_purpose(system_prompt: str, prompt: str, symbol: str | None) -> str:
        """system_prompt + prompt에서 LLM 호출 용도 + 종목명 자동 감지"""
        import re
        sp = system_prompt[:200].lower() if system_prompt else ""
        if "리스크" in sp or "한도" in sp:
            return "리스크 한도"
        if "스크리너" in sp or "스크리" in sp or "종목을 선별" in sp:
            return "시장 스캔"
        if "재평가" in sp or "hold/sell" in sp or "보유종목" in sp:
            return "보유 재평가"
        # 종목 분석: prompt에서 종목명 추출
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
        """LLM 프롬프트/응답을 activity log에 기록"""
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
        market_context: str = "", stock_baseline: str = "",
    ) -> tuple[str, str]:
        """Tier 1 (빠른 분석용)"""
        return await self.generate(
            prompt, LLMTier.TIER1, system_prompt,
            symbol=symbol, cycle_id=cycle_id,
            market_context=market_context, stock_baseline=stock_baseline,
        )

    async def generate_tier2(
        self, prompt: str, system_prompt: str = "",
        *, symbol: str | None = None, cycle_id: str | None = None,
        market_context: str = "", stock_baseline: str = "",
    ) -> tuple[str, str]:
        """Tier 2 (프리미엄 분석용)"""
        return await self.generate(
            prompt, LLMTier.TIER2, system_prompt,
            symbol=symbol, cycle_id=cycle_id,
            market_context=market_context, stock_baseline=stock_baseline,
        )

    def get_llm_status(self) -> dict:
        """현재 LLM 설정 상태 반환 (Admin API용)"""
        backend = settings.LLM_BACKEND

        if backend == "anthropic":
            tier1_model = settings.LLM_MODEL_TIER1
            tier2_model = settings.LLM_MODEL_TIER2
            active = {
                "backend": "anthropic",
                "tier1": {"provider": "ANTHROPIC", "model": tier1_model},
                "tier2": {"provider": "ANTHROPIC", "model": tier2_model},
                "session": None,  # stateless
                "has_key": bool(settings.ANTHROPIC_API_KEY),
            }
        else:  # claude_code
            tier1_model = settings.CLAUDE_CODE_MODEL_TIER1 or settings.CLAUDE_CODE_MODEL or "haiku"
            tier2_model = settings.CLAUDE_CODE_MODEL_TIER2 or settings.CLAUDE_CODE_MODEL or "sonnet"
            active = {
                "backend": "claude_code",
                "tier1": {"provider": "CLAUDE_CODE", "model": tier1_model},
                "tier2": {"provider": "CLAUDE_CODE", "model": tier2_model},
                "session": {
                    "id": ClaudeCodeProvider.get_session_id(),
                    "initialized": ClaudeCodeProvider._session_initialized,
                    "expired": ClaudeCodeProvider._is_session_expired(),
                },
                "has_key": True,
            }

        return {
            "active_backend": backend,
            **active,
            "health": {
                "available": self._failure_count == 0,
                "failure_count": self._failure_count,
                "last_error": self._last_error[:200] if self._last_error else None,
                "last_failure_at": self._last_failure_at or None,
            },
            "available_providers": [
                {
                    "id": "ANTHROPIC",
                    "name": "Anthropic API",
                    "models": {
                        "tier1": settings.LLM_MODEL_TIER1,
                        "tier2": settings.LLM_MODEL_TIER2,
                    },
                    "has_key": bool(settings.ANTHROPIC_API_KEY),
                },
                {
                    "id": "CLAUDE_CODE",
                    "name": "Claude Code (로컬)",
                    "models": {
                        "tier1": settings.CLAUDE_CODE_MODEL_TIER1 or settings.CLAUDE_CODE_MODEL,
                        "tier2": settings.CLAUDE_CODE_MODEL_TIER2 or settings.CLAUDE_CODE_MODEL,
                    },
                    "has_key": True,
                },
            ],
        }

    def get_usage_snapshot(self) -> dict:
        """active backend의 누적 사용량 스냅샷 — Admin `/llm/usage`용"""
        if settings.LLM_BACKEND == "anthropic":
            return AnthropicProvider.get_usage_snapshot()
        return ClaudeCodeProvider.get_usage_snapshot()


llm_factory = LLMFactory()
