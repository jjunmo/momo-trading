import pytest

import analysis.llm.llm_factory as llm_factory_module
from analysis.llm.llm_factory import LLMFactory
from core.config import settings
from trading.enums import LLMProvider, LLMTier


def make_dummy_provider(provider_name: LLMProvider, *, response: str, fail: bool = False):
    class DummyProvider:
        calls = []
        start_count = 0
        pause_count = 0
        resume_args = []
        end_count = 0
        _session_initialized = False
        _session_id = None

        def __init__(self, tier: LLMTier = LLMTier.TIER1):
            self._tier = tier
            self.model_id = f"{provider_name.value}:{tier.value}"

        @classmethod
        def start_session(cls):
            cls.start_count += 1
            cls._session_id = "session-1"
            cls._session_initialized = True
            return cls._session_id

        @classmethod
        def pause_session(cls):
            cls.pause_count += 1
            sid = cls._session_id
            cls._session_id = None
            return sid

        @classmethod
        def resume_session(cls, session_id: str):
            cls.resume_args.append(session_id)
            cls._session_id = session_id
            cls._session_initialized = True

        @classmethod
        def end_session(cls):
            cls.end_count += 1
            sid = cls._session_id
            cls._session_id = None
            cls._session_initialized = False
            return sid

        @classmethod
        def reset_session(cls):
            cls._session_id = None
            cls._session_initialized = False

        @classmethod
        def _rotate_session(cls, reason: str = ""):
            cls._session_id = "session-rotated"
            cls._session_initialized = False

        @classmethod
        def get_session_id(cls):
            return cls._session_id

        @classmethod
        def _is_session_expired(cls):
            return False

        @property
        def provider(self):
            return provider_name

        @property
        def tier(self):
            return self._tier

        async def is_available(self):
            return True

        async def generate(self, prompt: str, system_prompt: str = ""):
            self.__class__.calls.append((self._tier, prompt, system_prompt))
            if fail:
                raise RuntimeError("provider failure")
            return response

        @classmethod
        def get_usage_snapshot(cls):
            return {"total_calls": len(cls.calls)}

    return DummyProvider


@pytest.fixture(autouse=True)
def no_activity_logging(monkeypatch):
    async def fake_log(self, **kwargs):
        return None

    monkeypatch.setattr(LLMFactory, "_log_llm_conversation", fake_log)

    async def fast_sleep(seconds):
        return None

    monkeypatch.setattr(llm_factory_module.asyncio, "sleep", fast_sleep)


@pytest.mark.asyncio
async def test_factory_routes_to_selected_codex_provider(monkeypatch):
    codex = make_dummy_provider(LLMProvider.CODEX_CLI, response="codex-ok")
    claude = make_dummy_provider(LLMProvider.CLAUDE_CODE, response="claude-ok")

    monkeypatch.setattr(settings, "LLM_PROVIDER", "CODEX_CLI")
    monkeypatch.setattr(LLMFactory, "_provider_classes", {
        LLMProvider.CLAUDE_CODE: claude,
        LLMProvider.CODEX_CLI: codex,
    })

    factory = LLMFactory()
    result, provider = await factory.generate_tier1("prompt", "system")

    assert result == "codex-ok"
    assert provider == "CODEX_CLI"
    assert codex.calls == [(LLMTier.TIER1, "prompt", "system")]
    assert claude.calls == []


@pytest.mark.asyncio
async def test_factory_falls_back_to_claude_when_codex_fails(monkeypatch):
    codex = make_dummy_provider(LLMProvider.CODEX_CLI, response="", fail=True)
    claude = make_dummy_provider(LLMProvider.CLAUDE_CODE, response="claude-ok")

    monkeypatch.setattr(settings, "LLM_PROVIDER", "CODEX_CLI")
    monkeypatch.setattr(LLMFactory, "_provider_classes", {
        LLMProvider.CLAUDE_CODE: claude,
        LLMProvider.CODEX_CLI: codex,
    })

    factory = LLMFactory()
    result, provider = await factory.generate_tier2("prompt")

    assert result == "claude-ok"
    assert provider == "CLAUDE_CODE"
    assert len(codex.calls) == 3
    assert claude.calls == [(LLMTier.TIER2, "prompt", "")]


@pytest.mark.asyncio
async def test_factory_falls_back_to_codex_when_claude_fails(monkeypatch):
    """양방향 폴백 — Claude 선택 + Claude 실패 → Codex 폴백."""
    codex = make_dummy_provider(LLMProvider.CODEX_CLI, response="codex-ok")
    claude = make_dummy_provider(LLMProvider.CLAUDE_CODE, response="", fail=True)

    monkeypatch.setattr(settings, "LLM_PROVIDER", "CLAUDE_CODE")
    monkeypatch.setattr(LLMFactory, "_provider_classes", {
        LLMProvider.CLAUDE_CODE: claude,
        LLMProvider.CODEX_CLI: codex,
    })

    factory = LLMFactory()
    result, provider = await factory.generate_tier1("prompt", "sys")

    assert result == "codex-ok"
    assert provider == "CODEX_CLI"
    assert len(claude.calls) == 3  # 세션→새세션→stateless
    assert codex.calls == [(LLMTier.TIER1, "prompt", "sys")]


def test_factory_session_methods_delegate_to_selected_provider(monkeypatch):
    codex = make_dummy_provider(LLMProvider.CODEX_CLI, response="codex-ok")
    claude = make_dummy_provider(LLMProvider.CLAUDE_CODE, response="claude-ok")

    monkeypatch.setattr(settings, "LLM_PROVIDER", "CODEX_CLI")
    monkeypatch.setattr(LLMFactory, "_provider_classes", {
        LLMProvider.CLAUDE_CODE: claude,
        LLMProvider.CODEX_CLI: codex,
    })

    factory = LLMFactory()

    assert factory.start_session() == "session-1"
    assert factory.pause_session() == "session-1"
    factory.resume_session("session-2")
    assert factory.end_session() == "session-2"

    assert codex.start_count == 1
    assert codex.pause_count == 1
    assert codex.resume_args == ["session-2"]
    assert codex.end_count == 1
