"""Anthropic API Provider — `AsyncAnthropic` 기반 LLM 호출

- stateless: 세션 개념 없음. 연속 컨텍스트는 `cache_control`(프롬프트 캐싱)로 대체.
- Layered caching (요청당 최대 4개 cache_control breakpoint):
    L1 system        → 1h TTL
    L2a market_ctx   → 5min TTL
    L2b stock_base   → 1h TTL
    L3 fresh prompt  → 캐싱 안 함
- 폴백: SDK 내장 `max_retries` + exponential backoff. tier downgrade 없음.
"""
import time
from collections import defaultdict
from typing import Any

from loguru import logger

from core.config import settings
from trading.enums import LLMProvider, LLMTier


# 모델별 가격 (USD per 1M tokens) — 2026-04 기준, 변경 시 수정
# Anthropic 가격 변동이나 `*-latest` alias 변경으로 부정확할 수 있음
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1},
    "claude-haiku-latest": {"input": 1.0, "output": 5.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write_5m": 3.75, "cache_write_1h": 6.0, "cache_read": 0.3},
    "claude-sonnet-latest": {"input": 3.0, "output": 15.0, "cache_write_5m": 3.75, "cache_write_1h": 6.0, "cache_read": 0.3},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.5},
    "claude-opus-latest": {"input": 15.0, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.5},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int,
                   cache_read: int, cache_write_5m: int, cache_write_1h: int) -> float:
    """토큰 수로부터 USD 비용 추정. 미지 모델이면 Sonnet 가격으로 fallback."""
    price = None
    for prefix, p in _MODEL_PRICING.items():
        if model.startswith(prefix):
            price = p
            break
    if price is None:
        price = _MODEL_PRICING["claude-sonnet-latest"]
    return (
        input_tokens * price["input"]
        + output_tokens * price["output"]
        + cache_read * price["cache_read"]
        + cache_write_5m * price["cache_write_5m"]
        + cache_write_1h * price["cache_write_1h"]
    ) / 1_000_000


class AnthropicProvider:
    """Anthropic API를 AsyncAnthropic SDK로 호출

    - Tier1/Tier2는 클래스 인스턴스로 구분 (모델만 다름)
    - cumulative_usage는 클래스 변수로 전체 누적 관리 → ClaudeCodeProvider 스키마 동일
    """

    # 클래스 레벨 누적 사용량 (ClaudeCodeProvider와 동일 스키마 유지)
    cumulative_usage: dict = {
        "total_calls": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read": 0,
        "total_cache_creation": 0,
        "by_model": defaultdict(lambda: {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0,
        }),
    }

    # 공유 싱글톤 클라이언트 — Tier1/Tier2 간에 재사용 (connection pool 공유)
    _client: Any | None = None

    def __init__(self, tier: LLMTier = LLMTier.TIER1):
        self._tier = tier
        if tier == LLMTier.TIER1:
            self._model = settings.LLM_MODEL_TIER1
        else:
            self._model = settings.LLM_MODEL_TIER2
        self._resolved_model: str = ""

    @classmethod
    def _get_client(cls):
        """AsyncAnthropic 싱글톤"""
        if cls._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:
                raise RuntimeError(
                    "anthropic SDK 미설치. `pip install anthropic>=0.68.0` 실행 필요"
                ) from e
            cls._client = AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                max_retries=settings.LLM_MAX_RETRIES,
                timeout=float(settings.LLM_REQUEST_TIMEOUT_SEC),
            )
        return cls._client

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.ANTHROPIC

    @property
    def model_id(self) -> str:
        return self._resolved_model or self._model

    async def is_available(self) -> bool:
        return bool(settings.ANTHROPIC_API_KEY)

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        market_context: str = "",
        stock_baseline: str = "",
    ) -> str:
        """Anthropic API 호출

        Layered caching:
        - system_prompt → L1 (1h TTL)
        - market_context → L2a (5min TTL), 비어있으면 생략
        - stock_baseline → L2b (1h TTL), 비어있으면 생략
        - prompt → L3 (캐싱 없음)
        """
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY 미설정 — .env에 키 추가 필요")

        client = self._get_client()
        cache_on = settings.LLM_CACHE_ENABLED

        # ── system (L1) ──
        system_blocks: list[dict] = []
        if system_prompt:
            block: dict = {"type": "text", "text": system_prompt}
            if cache_on:
                block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            system_blocks.append(block)

        # ── user content ──
        # Anthropic 제약: 긴 TTL 블록이 짧은 TTL 블록보다 반드시 앞에 와야 함.
        # 따라서 L2b(1h) → L2a(5m) → L3(no cache) 순서로 배치.
        user_content: list[dict] = []
        if stock_baseline:
            block = {"type": "text", "text": stock_baseline}
            if cache_on:
                block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            user_content.append(block)
        if market_context:
            block = {"type": "text", "text": market_context}
            if cache_on:
                block["cache_control"] = {"type": "ephemeral"}  # 5min default
            user_content.append(block)
        # L3: 항상 존재, 캐싱 없음
        user_content.append({"type": "text", "text": prompt})

        kwargs: dict = {
            "model": self._model,
            "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
            "messages": [{"role": "user", "content": user_content}],
        }
        if system_blocks:
            kwargs["system"] = system_blocks

        start = time.time()
        try:
            response = await client.messages.create(**kwargs)
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("Anthropic API 호출 실패 ({}ms, model={}): {}: {}",
                         elapsed, self._model, type(e).__name__, str(e)[:200])
            raise

        # 응답 텍스트 추출
        result_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                result_text += block.text

        self._resolved_model = response.model or self._model
        self._track_usage(response)

        if not result_text.strip():
            raise RuntimeError("Anthropic 빈 응답")

        return result_text

    def _track_usage(self, response: Any) -> None:
        """response.usage에서 토큰 누적 (ClaudeCodeProvider 스키마 호환)"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
        # cache_creation_input_tokens: 5min·1h TTL 블록 합
        cache_c_total = getattr(usage, "cache_creation_input_tokens", 0) or 0
        # SDK가 5m/1h 분리 제공하면 활용, 아니면 전체 write를 1h로 간주(보수적 비용 추정)
        cache_creation_obj = getattr(usage, "cache_creation", None)
        if cache_creation_obj is not None:
            cache_c_5m = getattr(cache_creation_obj, "ephemeral_5m_input_tokens", 0) or 0
            cache_c_1h = getattr(cache_creation_obj, "ephemeral_1h_input_tokens", 0) or 0
        else:
            cache_c_5m = 0
            cache_c_1h = cache_c_total

        model_name = self._resolved_model or self._model
        cost = _estimate_cost(model_name, inp, out, cache_r, cache_c_5m, cache_c_1h)

        cls = self.__class__
        cls.cumulative_usage["total_calls"] += 1
        cls.cumulative_usage["total_cost_usd"] += cost
        cls.cumulative_usage["total_input_tokens"] += inp
        cls.cumulative_usage["total_output_tokens"] += out
        cls.cumulative_usage["total_cache_read"] += cache_r
        cls.cumulative_usage["total_cache_creation"] += cache_c_total

        m = cls.cumulative_usage["by_model"][model_name]
        m["calls"] += 1
        m["input_tokens"] += inp
        m["output_tokens"] += out
        m["cache_read"] += cache_r
        m["cache_creation"] += cache_c_total
        m["cost_usd"] += cost

    @classmethod
    def get_usage_snapshot(cls) -> dict:
        """현재 누적 사용량 스냅샷 (Admin `/llm/usage`용, CLI 스키마 호환)"""
        u = cls.cumulative_usage
        return {
            "total_calls": u["total_calls"],
            "total_cost_usd": round(u["total_cost_usd"], 4),
            "total_input_tokens": u["total_input_tokens"],
            "total_output_tokens": u["total_output_tokens"],
            "total_cache_read": u["total_cache_read"],
            "total_cache_creation": u["total_cache_creation"],
            "by_model": {
                model: {**stats}
                for model, stats in u["by_model"].items()
            },
            "session_id": None,  # stateless
        }
