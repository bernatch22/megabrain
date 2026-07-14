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

    def tool(name, description, schema):
        def deco(fn):
            return {"name": name, "description": description,
                    "schema": schema, "handler": fn}
        return deco

    def create_sdk_mcp_server(*, name, version, tools):
        calls["mcp_tools"] = tools
        return {"name": name, "version": version, "tools": tools}

    mod = types.ModuleType("claude_agent_sdk")
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.query = query
    mod.tool = tool
    mod.create_sdk_mcp_server = create_sdk_mcp_server
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
    monkeypatch.setenv("MEGABRAIN_ASK_MODEL", "claude-sonnet-4-5")
    assert providers.ask_model() == "claude-sonnet-4-5"
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "openrouter")
    monkeypatch.delenv("MEGABRAIN_ASK_MODEL", raising=False)
    assert providers.ask_model() == "google/gemini-3-flash-preview"


def test_missing_sdk_error_is_actionable(monkeypatch):
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # import -> ImportError
    with pytest.raises(RuntimeError, match="megabrain\\[claude\\]"):
        providers.stream_chat({"model": "haiku", "messages": []})


def test_agent_stream_registers_tools_and_unpins_narration(fake_sdk):
    """ask v2 sub-agent turn: retrieval tools become an in-process MCP server,
    only those tools are allowed (builtins stay disallowed), no no-tools
    preamble, and the handler routes to megabrain's sync backend."""
    import asyncio

    from megabrain.providers import claude as providers_claude
    tools = [{"name": "search_more", "description": "d",
              "schema": {"type": "object"}, "fn": lambda a: f"R:{a['query']}"}]
    text = providers_claude.agent_stream("PROMPT", model="haiku", tools=tools)
    assert text == "Hello [[0]]"
    opts = fake_sdk["options"]
    assert opts["allowed_tools"] == ["mcp__megabrain__search_more"]
    assert "megabrain" in opts["mcp_servers"]
    assert opts["max_turns"] == 8
    assert "Bash" in opts["disallowed_tools"]
    assert fake_sdk["prompt"] == "PROMPT"       # raw — the agent path has tools
    h = fake_sdk["mcp_tools"][0]["handler"]
    assert asyncio.run(h({"query": "x"})) == \
        {"content": [{"type": "text", "text": "R:x"}]}


def test_stream_chat_with_tools_flag_returns_empty_calls(fake_sdk):
    """providers.stream_chat(with_tools=True) on the claude route: the SDK runs
    its own tool loop, so the 3-tuple always carries no tool_calls."""
    text, stop, calls = providers.stream_chat(
        {"model": "haiku", "messages": [{"role": "user", "content": "Q"}]},
        with_tools=True)
    assert text == "Hello [[0]]" and calls == []
