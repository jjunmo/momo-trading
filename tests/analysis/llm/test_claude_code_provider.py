"""Claude Code provider가 Codex provider와 동일한 구조로 작동하는지 검증."""

from analysis.llm.claude_code_provider import ClaudeCodeProvider
from core.config import settings
from trading.enums import LLMProvider, LLMTier


def test_claude_uses_effort_from_settings(monkeypatch):
    """claude_code_provider.py 하드코드 제거 후 settings 값을 사용해야 함."""
    monkeypatch.setattr(settings, "LLM_EFFORT_TIER1", "low")
    ClaudeCodeProvider.reset_session()
    provider = ClaudeCodeProvider(LLMTier.TIER1)
    provider._claude_path = "/usr/local/bin/claude"

    cmd = provider._build_command()

    assert "--effort" in cmd
    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "low"


def test_claude_effort_xhigh_maps_to_high(monkeypatch):
    """Claude CLI는 xhigh를 받지 못하므로 high로 매핑되어야 함."""
    monkeypatch.setattr(settings, "LLM_EFFORT_TIER2", "xhigh")
    ClaudeCodeProvider.reset_session()
    provider = ClaudeCodeProvider(LLMTier.TIER2)
    provider._claude_path = "/usr/local/bin/claude"

    cmd = provider._build_command()

    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "high"


def test_claude_disallowed_tools_includes_read():
    """도구 사용 완전 차단 — Read도 명시적으로 disallow."""
    ClaudeCodeProvider.reset_session()
    provider = ClaudeCodeProvider(LLMTier.TIER1)
    provider._claude_path = "/usr/local/bin/claude"

    cmd = provider._build_command()

    assert "--disallowedTools" in cmd
    tools_idx = cmd.index("--disallowedTools")
    tools = cmd[tools_idx + 1]
    assert "Read" in tools
    assert "Bash" in tools
    assert "Write" in tools


def test_claude_prompt_body_contains_role_even_on_first_call():
    """첫 호출에서도 system_prompt는 본문 [역할]/[요청] 형식으로 포함 (Codex와 동일)."""
    ClaudeCodeProvider.reset_session()
    ClaudeCodeProvider.start_session()
    provider = ClaudeCodeProvider(LLMTier.TIER1)
    provider._claude_path = "/usr/local/bin/claude"

    body = provider._build_prompt("분석해줘", "너는 트레이딩 분석가야")
    cmd = provider._build_command()

    # system_prompt 플래그 완전 제거 — 본문에만 포함
    assert "--system-prompt" not in cmd
    assert body.startswith("[역할]\n너는 트레이딩 분석가야")
    assert "[요청]\n분석해줘" in body

    ClaudeCodeProvider.reset_session()


def test_claude_prompt_body_empty_system_prompt():
    ClaudeCodeProvider.reset_session()
    provider = ClaudeCodeProvider(LLMTier.TIER1)

    body = provider._build_prompt("단독 요청", "")

    assert body == "단독 요청"


def test_claude_provider_metadata():
    provider = ClaudeCodeProvider(LLMTier.TIER1)

    assert provider.provider == LLMProvider.CLAUDE_CODE
    assert provider.tier == LLMTier.TIER1
