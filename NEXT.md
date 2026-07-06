# NEXT — after `ask v2`

`ask v2` (adaptive multi-agent synthesis) **shipped 2026-07-06**. What exists now:

- **`megabrain/ask_agents.py`** — `classify_bundle` (no-LLM broad/scoped from the
  bundle shape) · `repo_map` (paths + skeleton doclines, in every agent's prompt) ·
  `_plan` (one rerank_model call → ≤4 scoped slices; fail-open to top-level-dir
  clustering) · parallel sub-agents with tools (`search_more`/`get_file`/`get_symbol`
  — backends are `search_with_state`/`get_code`, no LLM) · streamed synthesis with
  GLOBAL `[[k]]` citations so the existing splice pipeline grounds code unchanged ·
  `stream_events`, the single event driver every surface sinks from.
- **Providers**: `stream_chat(with_tools=True)` parses fragmented OpenAI
  `tool_calls` deltas; `claude.agent_stream` registers the tools as an in-process
  SDK MCP server (the SDK runs the tool loop; builtins stay disallowed).
- **Surfaces**: CLI `ask --agents/--no-agents` (default AUTO) with live status
  lines · MCP `megabrain_ask(agents?)` buffered + trace footer · serve-api
  `POST /ask/stream` (SSE) · `examples/webui` Explain overlay: one card per
  sub-agent streaming prose + tool calls, minimizing on done, synthesis below.
- **Guardrails kept**: no LLM anywhere in retrieval/tools; fail-open chain
  (fan-out → single-agent → bundle); ≤4 agents, ≤3 tool rounds; scoped questions
  never fan out. Gates green: full pytest suite + golden (bundle_full **1.00**,
  R@1 0.86) + multi + scale.

## Next

1. **Port the multi-agent live view to `bernardocastro.dev/megabrain`** — the demo
   backend (`services/megabrain/server.py`, stdlib proxy on :2137) needs an SSE
   pass-through to serve-api's `/ask/stream`, and `Megabrain.astro` the agent-card
   UI (the `examples/webui/ui/index.html` implementation is the reference — same
   events, same minimize-on-done behavior). Engine side is done.
2. **Classifier-threshold eval** — collect real broad/scoped query pairs and
   measure `classify_bundle` precision; thresholds today (tier1≥3 within gap,
   dirs≥3, parity≥4 @0.92, issue-length>25) were reasoned, not tuned.
3. **Carried over from before ask v2**: `.tsx` arrow-component symbols · SWE-bench
   ask eval.
