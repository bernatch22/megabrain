"""MEGABRAIN_CHAT_PROVIDER=claude — routing + streaming through a fake Agent
SDK (offline). The real SDK spawns the Claude Code CLI; here we only verify
megabrain's side of the contract: body -> prompt mapping, delta streaming,
finish_reason, key sentinel, and per-provider model defaults."""

import sys
import types

import pytest

from megabrain import providers


class StreamEvent:                         # names matter: matched by class name
    def __init__(self, event):
        self.event = event


class _TextBlock:
    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, text):
        self.content = [_TextBlock(text)]


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install a fake `claude_agent_sdk` that streams two deltas."""
    calls = {}

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            calls["options"] = kw

    async def query(*, prompt, options):
        calls["prompt"] = prompt
        yield StreamEvent({"type": "content_block_delta",
                           "delta": {"type": "text_delta", "text": "Hello "}})
        yield StreamEvent({"type": "content_block_delta",
                           "delta": {"type": "text_delta", "text": "[[0]]"}})
        yield StreamEvent({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
        yield AssistantMessage("Hello [[0]]")   # ignored: deltas already streamed

    mod = types.ModuleType("claude_agent_sdk")
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    return calls


def test_stream_chat_routes_and_streams(fake_sdk):
    deltas = []
    text, stop = providers.stream_chat(
        {"model": "haiku", "messages": [{"role": "user", "content": "QUESTION"}]},
        on_delta=deltas.append)
    assert text == "Hello [[0]]"
    assert deltas == ["Hello ", "[[0]]"]
    assert stop == ""                       # end_turn, not a length cap
    # the user content arrives intact, behind the transport's no-tools preamble
    assert fake_sdk["prompt"].endswith("QUESTION")
    assert "NO tools" in fake_sdk["prompt"]
    opts = fake_sdk["options"]
    assert opts["model"] == "haiku"
    assert opts["allowed_tools"] == [] and opts["max_turns"] == 2
    assert "Bash" in opts["disallowed_tools"]


def test_chat_text_routes(fake_sdk):
    assert providers.chat_text("haiku", "rank these", max_tokens=100) == "Hello [[0]]"


def test_key_sentinel_and_model_defaults(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    monkeypatch.delenv("MEGABRAIN_ASK_MODEL", raising=False)
    assert providers.find_chat_key(required=True) == "claude"   # no key needed
    assert providers.ask_model() == "haiku"
    assert providers.rerank_model() == "haiku"
    monkeypatch.setenv("MEGABRAIN_ASK_MODEL", "claude-sonnet-4-5")
    assert providers.ask_model() == "claude-sonnet-4-5"
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "openrouter")
    monkeypatch.delenv("MEGABRAIN_ASK_MODEL", raising=False)
    assert providers.ask_model() == "qwen/qwen3-coder"


def test_missing_sdk_error_is_actionable(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # import -> ImportError
    with pytest.raises(RuntimeError, match="megabrain\\[claude\\]"):
        providers.stream_chat({"model": "haiku", "messages": []})
