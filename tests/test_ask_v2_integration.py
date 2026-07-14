"""ask v2 INTEGRATION — the full fan-out over a REAL indexed repo (tiny_repo +
FakeEmbedder from conftest), on BOTH chat providers, offline.

Everything except the LLM is real: retrieval runs on the indexed tiny_repo, the
planner call goes through providers.chat_text (returns garbage -> the
deterministic dir-clustering fallback plans the agents), sub-agents run in the
real ThreadPool with the REAL tool backends (search_more re-queries the repo),
and synthesis splices REAL code from disk via the global [[k]] citations.

- OpenRouter: urllib is monkeypatched with an SSE router that plays a
  tool-calling sub-agent (round 1: search_more tool_call; round 2: cite) and a
  synthesizer that preserves every [[k]] it received.
- Claude: a fake claude_agent_sdk plays the SDK's own tool loop — it CALLS the
  registered in-process MCP tool handler, then narrates with the citation.
"""

import json
import re
import sys
import types

import pytest

from megabrain.ask import ask, render_ask
from megabrain.ask.agents import stream_events

QUESTION = "how do login auth, invoices and utils work together end to end"


def _assigned_k(prompt: str) -> str:
    """First chunk index ASSIGNED to a sub-agent — anchored to its 'your own
    chunks' line (a bare [[\\d]] regex would match the [[3]] example in _RULES)."""
    return re.search(r"your own chunks: \[\[(\d+)\]\]", prompt).group(1)


def _partial_ks(prompt: str) -> list[int]:
    """Citations inside the PARTIAL WALKTHROUGHS block only (not the rules)."""
    tail = prompt.split("PARTIAL WALKTHROUGHS", 1)[1]
    return sorted({int(k) for k in re.findall(r"\[\[(\d+)\]\]", tail)})


# ── OpenRouter fake: an SSE chat router keyed on the request body ──────────

class _Resp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __iter__(self):
        return iter(self._p.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(deltas=(), tool_calls=None, finish="stop") -> bytes:
    lines = []
    for d in deltas:
        lines.append(("data: " + json.dumps(
            {"choices": [{"delta": {"content": d}}]}) + "\n").encode())
    if tool_calls:
        tc = [{"index": i, "id": f"call_{i}",
               "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
              for i, c in enumerate(tool_calls)]
        lines.append(("data: " + json.dumps(
            {"choices": [{"delta": {"tool_calls": tc}}]}) + "\n").encode())
        finish = "tool_calls"
    lines.append(("data: " + json.dumps(
        {"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n").encode())
    lines.append(b"data: [DONE]\n")
    return b"".join(lines)


@pytest.fixture
def fake_openrouter(monkeypatch):
    """Route every chat call by shape: planner (non-stream) -> garbage (forces
    the clustering fallback); sub-agent round 1 -> a search_more tool_call;
    round 2 -> cites its first assigned chunk; synthesis -> re-emits every
    [[k]] found in the partials."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    stats = {"plan": 0, "tool_rounds": 0, "cite_rounds": 0, "synth": 0}

    def urlopen(req, timeout=0):
        body = json.loads(req.data)
        msgs = body["messages"]
        prompt0 = msgs[0]["content"]
        if not body.get("stream"):
            stats["plan"] += 1        # chat_text = the planner
            return _Resp(json.dumps(
                {"choices": [{"message": {"content": "no json here"}}]}).encode())
        if "PARTIAL WALKTHROUGHS" in prompt0:
            stats["synth"] += 1
            return _Resp(_sse(["## Merged\n"] +
                              [f"Part {k}.\n[[{k}]]\n" for k in _partial_ks(prompt0)]))
        if any(m.get("role") == "tool" for m in msgs):
            stats["cite_rounds"] += 1  # tool result came back -> answer + cite
            return _Resp(_sse([f"Slice explained.\n[[{_assigned_k(prompt0)}]]\n"]))
        stats["tool_rounds"] += 1      # round 1: ask for more context
        return _Resp(_sse(tool_calls=[
            {"name": "search_more", "args": {"query": "invoice login util"}}]))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return stats


def _check_fanout(events, out, n_expected_min=2):
    types_ = [e["type"] for e in events]
    assert "plan" in types_
    n = types_.count("agent_start")
    assert n >= n_expected_min
    assert types_.count("agent_done") == n
    tool_evs = [e for e in events if e["type"] == "agent_tool"]
    assert tool_evs and all(e["tool"] == "search_more" for e in tool_evs)
    # the REAL tool backend ran retrieval on the repo and returned content
    assert all(e["chars"] > 0 for e in events if e["type"] == "agent_tool_done")
    assert any(e["type"] == "synthesis_start" and e["agents"] == n for e in events)
    spliced = "".join(e["text"] for e in events if e["type"] == "synthesis_delta")
    # global [[k]] citations survived synthesis and spliced VERBATIM disk code
    assert "def login_user" in spliced or "def check_password" in spliced
    assert "def create_invoice" in spliced
    done = events[-1]
    assert done["type"] == "done" and len(done["agents"]) == n
    assert out["agents"] and len(out["agents"]) == n
    assert out["text"].count("[[") >= 2


def test_openrouter_orchestration_and_synthesis(tiny_repo, fake_openrouter):
    events = []
    out = stream_events(tiny_repo, QUESTION, events.append, agents=True)
    _check_fanout(events, out)
    assert fake_openrouter["plan"] == 1
    assert fake_openrouter["synth"] == 1
    assert fake_openrouter["tool_rounds"] == fake_openrouter["cite_rounds"]


def test_openrouter_buffered_ask_and_mcp_footer(tiny_repo, fake_openrouter):
    """The buffered path (what MCP/POST /ask use): ask(agents=True) -> trace in
    the dict, render_ask splices, and the MCP tool appends the agent footer."""
    out = ask(tiny_repo, QUESTION, agents=True)
    assert out["agents"] and len(out["agents"]) >= 2
    rendered = render_ask(out)
    assert "def create_invoice" in rendered
    from megabrain.server.mcp import call_tool
    text = call_tool("megabrain_ask", {"repo_path": str(tiny_repo),
                                       "question": QUESTION, "agents": True})
    assert "— multi-agent: " in text
    assert "def create_invoice" in text


# ── Claude fake: the SDK runs its own tool loop ────────────────────────────

@pytest.fixture
def fake_claude_sdk(monkeypatch):
    """A claude_agent_sdk that behaves like the real agent runtime: on a
    sub-agent turn it CALLS the registered mcp tool handler (which executes
    megabrain's real search_more on the repo), then narrates with the agent's
    first assigned citation."""
    stats = {"planner": 0, "subagents": 0, "synth": 0, "tool_results": []}

    class StreamEvent:
        def __init__(self, event):
            self.event = event

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            stats.setdefault("options", []).append(kw)

    def tool(name, description, schema):
        def deco(fn):
            return {"name": name, "description": description,
                    "schema": schema, "handler": fn}
        return deco

    def create_sdk_mcp_server(*, name, version, tools):
        return {"name": name, "version": version, "tools": tools}

    def _ev(text):
        return StreamEvent({"type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": text}})

    async def query(*, prompt, options):
        if "Reply ONLY JSON" in prompt:
            stats["planner"] += 1
            yield _ev("not json")          # planner -> clustering fallback
            return
        if "PARTIAL WALKTHROUGHS" in prompt:
            stats["synth"] += 1
            for k in _partial_ks(prompt):
                yield _ev(f"Part {k}.\n[[{k}]]\n")
            return
        stats["subagents"] += 1            # sub-agent turn: run the tool loop
        server = (getattr(options, "mcp_servers", None) or {}).get("megabrain")
        assert server, "sub-agent turn must carry the in-process MCP server"
        handler = next(t["handler"] for t in server["tools"]
                       if t["name"] == "search_more")
        res = await handler({"query": "invoice login util"})
        stats["tool_results"].append(len(res["content"][0]["text"]))
        yield _ev(f"Slice explained.\n[[{_assigned_k(prompt)}]]\n")

    mod = types.ModuleType("claude_agent_sdk")
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.query = query
    mod.tool = tool
    mod.create_sdk_mcp_server = create_sdk_mcp_server
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    monkeypatch.setenv("MEGABRAIN_CHAT_PROVIDER", "claude")
    return stats


def test_claude_orchestration_and_synthesis(tiny_repo, fake_claude_sdk):
    events = []
    out = stream_events(tiny_repo, QUESTION, events.append, agents=True)
    _check_fanout(events, out)
    st = fake_claude_sdk
    assert st["planner"] == 1 and st["synth"] == 1
    assert st["subagents"] >= 2
    assert st["tool_results"] and all(n > 0 for n in st["tool_results"])
    # every sub-agent turn allowed ONLY megabrain's tools, builtins denied
    agent_opts = [o for o in st["options"] if o.get("mcp_servers")]
    assert agent_opts and all(
        o["allowed_tools"] == ["mcp__megabrain__search_more",
                               "mcp__megabrain__get_file",
                               "mcp__megabrain__get_symbol"]
        and "Bash" in o["disallowed_tools"] for o in agent_opts)


def test_claude_buffered_ask(tiny_repo, fake_claude_sdk):
    out = ask(tiny_repo, QUESTION, agents=True)
    assert out["agents"] and len(out["agents"]) >= 2
    rendered = render_ask(out)
    assert "def create_invoice" in rendered
