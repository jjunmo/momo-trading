from analysis.llm.codex_cli_provider import CodexCLIProvider
from core.config import settings
from trading.enums import LLMProvider, LLMTier


def test_codex_command_uses_ephemeral_when_session_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER1", "gpt-5.4-mini")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1", "medium")
    monkeypatch.setattr(settings, "CODEX_DISABLE_MCP", True)

    CodexCLIProvider.reset_session()
    provider = CodexCLIProvider(LLMTier.TIER1)
    provider._codex_path = "/usr/local/bin/codex"

    cmd = provider._build_command(str(tmp_path / "out.txt"))

    assert cmd[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ephemeral" in cmd
    assert "--model" in cmd
    assert "gpt-5.4-mini" in cmd
    assert "model_reasoning_effort=medium" in cmd
    assert "mcp_servers={}" in cmd
    assert cmd[-1] == "-"


def test_codex_command_resumes_existing_session(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER2", "gpt-5.4")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER2", "xhigh")
    monkeypatch.setattr(settings, "CODEX_DISABLE_MCP", True)

    CodexCLIProvider.resume_session("thread-123")
    provider = CodexCLIProvider(LLMTier.TIER2)
    provider._codex_path = "/usr/local/bin/codex"

    cmd = provider._build_command(str(tmp_path / "out.txt"))

    assert cmd[:3] == ["/usr/local/bin/codex", "exec", "resume"]
    assert "--ephemeral" not in cmd
    assert "thread-123" in cmd
    assert "model_reasoning_effort=xhigh" in cmd
    assert "mcp_servers={}" in cmd
    assert cmd[-1] == "-"

    CodexCLIProvider.reset_session()


def test_codex_jsonl_parser_extracts_thread_text_and_usage():
    provider = CodexCLIProvider(LLMTier.TIER1)
    raw = "\n".join([
        '{"type":"thread.started","thread_id":"thread-abc"}',
        '{"type":"item.completed","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"BUY"}]}}',
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}',
    ])

    events = provider._parse_jsonl(raw)

    assert events["thread_id"] == "thread-abc"
    assert events["text"] == "BUY"
    assert events["usage"]["input_tokens"] == 10


def test_codex_provider_metadata():
    provider = CodexCLIProvider(LLMTier.TIER1)

    assert provider.provider == LLMProvider.CODEX_CLI
    assert provider.tier == LLMTier.TIER1
    assert provider.model_id.startswith("codex:")
