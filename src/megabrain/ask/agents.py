"""ask v2 — adaptive multi-agent synthesis over one retrieval bundle.

When a question is BROAD (flat tier1, candidates spread across subsystems,
many near-parity RELATED files), one narrator dilutes: it must cover several
subsystems in a single pass. ask v2 fans out: a planner (one cheap LLM call,
the ask model) splits the bundle into <=MAX_AGENTS scoped slices, parallel
sub-agents each explain their slice — with retrieval TOOLS they may call on
demand (search_more / get_file / get_symbol; the tools themselves stay
no-LLM) — and a synthesizer merges the partials into ONE walkthrough.

Citations are GLOBAL [[k]] indices into the shared candidate list, so the
existing splice pipeline (ask._SEL / ask._code_block / ask._Splicer) grounds
every code block verbatim from disk, unchanged.

Fail-open chain: planner fails -> deterministic dir clustering -> caller falls
back to single-agent ask -> full bundle. Any sub-agent may fail (the rest
proceed). Scoped questions never pay the fan-out: classify_bundle gates it.

Everything emits JSON-serializable events through on_event so a UI can watch
the agents work in parallel (CLI status lines, SSE for the web demo). Buffered
callers (MCP tools, POST /ask) just take the returned dict — an MCP tool call
is request/response, so streaming adds nothing there.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import providers
from ..retrieval.bundle import search_with_state
from ..retrieval.files import get_code
from ..retrieval.render import render
from ..retrieval.state import load_state

log = logging.getLogger(__name__)

MAX_AGENTS = 4        # hard cap on fan-out width (cost guard)
TOOL_ROUNDS = 3       # max tool-call rounds per sub-agent
TOOL_CAP = 8_000      # chars per tool result fed back to an agent
MAP_CHARS = 6_000     # repo-map budget in every agent's prompt
SUB_MAX_TOKENS = 1400
SYNTH_MAX_TOKENS = 2400
PLAN_TIMEOUT = 30     # s — a slow planner falls open to dir clustering (the
                      # claude transport spawns a full CLI, which can stall)
AGENT_TIMEOUT = 300   # s — a hung sub-agent dies alone; the rest proceed


def _top_dir(f: str) -> str:
    return f.split("/", 1)[0] if "/" in f else "."


def classify_bundle(res: dict, question: str = "") -> dict:
    """Scoped vs broad from the SHAPE of the bundle alone — no LLM, ~0ms.

    Scoped: one dominant CORE file (TIER1_GAP already demoted the rest).
    Broad: several files survived the gap cut (their scores are within 3% by
    construction), the top candidates spread across top-level dirs, many
    RELATED files sit near score parity with the top, or the query is
    issue-length. Any signal -> broad (the fan-out is still capped and
    fail-open, so a false positive costs latency, never correctness)."""
    t1, t2 = res.get("tier1") or [], res.get("tier2") or []
    reasons = []
    if len(t1) >= 3:
        reasons.append(f"{len(t1)} CORE files within the tier1 gap")
    dirs = {_top_dir(t["file"]) for t in t1} | {_top_dir(t["file"]) for t in t2[:6]}
    if len(t1) >= 2 and len(dirs) >= 3:
        reasons.append(f"candidates span {len(dirs)} top-level dirs")
    if t1:
        top = t1[0]["score"]
        near = sum(1 for t in t2 if not t.get("via_graph") and t["score"] >= 0.92 * top)
        if near >= 4:
            reasons.append(f"{near} RELATED files near score parity")
    if question:
        from ..retrieval.scoring import ident_tokens
        if len(ident_tokens(question)) > 25:
            reasons.append("issue-length query")
    return {"broad": bool(reasons), "reasons": reasons}


def repo_map(st, max_chars: int = MAP_CHARS) -> str:
    """Compact whole-repo orientation for every agent's prompt: each indexed
    path + the first line of its skeleton (a one-line summary), budget-capped
    (doclines drop first, then the file list truncates with a count)."""
    entries = sorted(zip(st.fpaths, st.fskels))
    lines = []
    for p, skel in entries:
        doc = next((ln.strip() for ln in (skel or "").splitlines() if ln.strip()), "")
        lines.append(f"{p} — {doc[:70]}" if doc else p)
    out = "\n".join(lines)
    if len(out) <= max_chars:
        return out
    paths = [e[0] for e in entries]
    out = "\n".join(paths)
    if len(out) <= max_chars:
        return out
    keep, used = [], 0
    for ln in paths:
        if used + len(ln) + 1 > max_chars - 30:
            break
        keep.append(ln)
        used += len(ln) + 1
    keep.append(f"… +{len(paths) - len(keep)} more files")
    return "\n".join(keep)


def _chunk_lines(cands: list[dict]) -> list[str]:
    return [f'[{k}] {c["file"]} L{c["start_line"]}-{c["end_line"]}'
            + (f' ({c["name"]})' if c["name"] else "")
            for k, c in enumerate(cands)]


# ── planner ────────────────────────────────────────────────────────────────

def _plan_llm(question: str, cands: list[dict], rmap: str,
              max_agents: int, model: str) -> list[dict] | None:
    """One cheap LLM call (the ask model) -> [{label, sub_query, chunks}].
    JSON-parse fail-open: any problem -> None."""
    idx = "\n".join(_chunk_lines(cands))
    prompt = f"""Split this developer question into focused sub-questions for a team of parallel code-explainer agents, and assign each agent the retrieved chunks it needs.

QUESTION: {question}

REPO MAP:
{rmap}

RETRIEVED CHUNKS (number · file · lines · symbol):
{idx}

Reply ONLY JSON: {{"agents": [{{"label": "short-kebab-name", "sub_query": "...", "chunks": [0, 2]}}]}}
Rules: 2-{max_agents} agents, grouped by subsystem/theme; every agent gets >=1 chunk; assign each chunk to AT MOST one agent (drop only chunks irrelevant to the question); sub_query is a scoped version of the question, answerable from that agent's chunks alone."""
    try:
        text = providers.chat_text(model, prompt,
                                   max_tokens=600, timeout=PLAN_TIMEOUT)
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        raw = json.loads(m.group(0)).get("agents") or []
    except Exception:
        log.debug("planner call failed", exc_info=True)
        return None
    seen: set[int] = set()
    plan = []
    for a in raw[:max_agents]:
        ids = []
        for k in a.get("chunks") or []:
            if isinstance(k, int) and 0 <= k < len(cands) and k not in seen:
                seen.add(k)                     # each chunk goes to ONE agent, once
                ids.append(k)
        if not ids:
            continue
        plan.append({"label": str(a.get("label") or f"agent-{len(plan) + 1}")[:40],
                     "sub_query": str(a.get("sub_query") or question)[:300],
                     "chunks": ids})
    return plan if len(plan) >= 2 else None


def _plan_cluster(question: str, cands: list[dict],
                  max_agents: int) -> list[dict] | None:
    """Deterministic fallback: group chunks by top-level dir (the natural
    subsystem boundary), fold the tail into the last slot. None when the
    bundle lives in a single dir — fan-out would add nothing."""
    groups: dict[str, list[int]] = {}
    for k, c in enumerate(cands):
        groups.setdefault(_top_dir(c["file"]), []).append(k)
    if len(groups) < 2:
        return None
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    keep = ordered[:max_agents]
    if len(ordered) > max_agents:
        extra = [k for _, ks in ordered[max_agents:] for k in ks]
        keep[-1] = (keep[-1][0] + "+misc", keep[-1][1] + extra)
    return [{"label": d, "sub_query": f"{question} — focus on {d}", "chunks": ks}
            for d, ks in keep]


def _plan(question: str, cands: list[dict], rmap: str, key: str | None,
          model: str | None = None, max_agents: int = MAX_AGENTS) -> list[dict] | None:
    model = model or providers.ask_model()
    plan = _plan_llm(question, cands, rmap, max_agents, model) if key else None
    return plan or _plan_cluster(question, cands, max_agents)


# ── tools (no LLM anywhere in these — rule 1 of the engine) ────────────────

def _tools(root: Path) -> list[dict]:
    """Retrieval tool backends shared by every sub-agent of one run. The
    search state loads lazily on the first search_more (many runs never call a
    tool) with check_same_thread=False + a lock, since agents run in worker
    threads. get_file/get_symbol open their own Store per call (thread-safe)."""
    holder: dict = {"st": None}
    lock = threading.Lock()

    def search_more(args: dict) -> str:
        q = str(args.get("query") or "").strip()
        if not q:
            return "empty query"
        with lock:
            if holder["st"] is None:
                holder["st"] = load_state(root, check_same_thread=False)
            res = search_with_state(holder["st"], q)
        return render(res, compact=True)[:TOOL_CAP]

    def get_file(args: dict) -> str:
        return get_code(root, str(args.get("path") or ""))[:TOOL_CAP]

    def get_symbol(args: dict) -> str:
        return get_code(root, str(args.get("path") or ""),
                        str(args.get("name") or "") or None)[:TOOL_CAP]

    return [
        {"name": "search_more",
         "description": "Semantic search over the whole repo — returns a compact "
                        "ranked map of matching files/chunks (no LLM, ~200ms).",
         "schema": {"type": "object",
                    "properties": {"query": {"type": "string",
                                             "description": "natural-language question or feature description"}},
                    "required": ["query"]},
         "fn": search_more},
        {"name": "get_file",
         "description": "Full source of one repo file.",
         "schema": {"type": "object",
                    "properties": {"path": {"type": "string",
                                            "description": "repo-relative path"}},
                    "required": ["path"]},
         "fn": get_file},
        {"name": "get_symbol",
         "description": "Source of one symbol (function/class/method) in a file.",
         "schema": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "name": {"type": "string",
                                            "description": "symbol name, e.g. Service.handle"}},
                    "required": ["path", "name"]},
         "fn": get_symbol},
    ]


def _wrap_tools(tools: list[dict], emit, aid: int) -> list[dict]:
    """Per-agent wrapper: every call surfaces as agent_tool / agent_tool_done
    events (the UI shows tool activity live) and never raises into the LLM loop."""
    out = []
    for t in tools:
        def fn(args: dict, _t=t) -> str:
            emit({"type": "agent_tool", "id": aid, "tool": _t["name"], "args": args})
            try:
                r = _t["fn"](args or {})
            except Exception as e:  # noqa: BLE001 — tool errors go back as text
                r = f"tool error: {e}"
            emit({"type": "agent_tool_done", "id": aid, "tool": _t["name"],
                  "chars": len(r)})
            return r
        out.append({**t, "fn": fn})
    return out


# ── LLM transports for one tool-enabled agent turn ─────────────────────────

def _openrouter_agent(prompt: str, tools: list[dict], key: str | None,
                      on_delta, max_tokens: int, model: str) -> str:
    """OpenAI function-calling loop: stream, execute tool_calls, feed results
    back, repeat (<=TOOL_ROUNDS); the final round carries no tools so the
    model must answer."""
    spec = [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["schema"]}} for t in tools]
    fns = {t["name"]: t["fn"] for t in tools}
    msgs: list[dict] = [{"role": "user", "content": prompt}]
    total = ""
    for rnd in range(TOOL_ROUNDS + 1):
        body = {"model": model, "max_tokens": max_tokens,
                "temperature": 0, "stream": True, "messages": msgs}
        if rnd < TOOL_ROUNDS:
            body["tools"] = spec
        text, _finish, calls = providers.stream_chat(body, key, on_delta=on_delta,
                                                     with_tools=True)
        total += text
        if not calls:
            return total
        msgs.append({"role": "assistant", "content": text or None,
                     "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                     "function": {"name": c["name"],
                                                  "arguments": c["arguments"] or "{}"}}
                                    for i, c in enumerate(calls)]})
        for i, c in enumerate(calls):
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = fns.get(c["name"])
            outp = fn(args) if fn else f"unknown tool {c['name']}"
            msgs.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}",
                         "content": outp})
    return total


def _agent_llm(prompt: str, tools: list[dict], key: str | None,
               on_delta, max_tokens: int, model: str) -> str:
    """One tool-enabled agent turn. CAPABILITY PROBE, not a name switch: a
    provider with a native tool loop (p.agent_stream — e.g. the Claude Agent
    SDK registers the tools as an in-process MCP server) runs it itself; any
    OpenAI-compatible provider gets the function-calling loop here."""
    p = providers.resolve()
    if p.agent_stream is not None:
        return p.agent_stream(prompt, model=model, tools=tools,
                              on_delta=on_delta, timeout=AGENT_TIMEOUT)
    return _openrouter_agent(prompt, tools, key, on_delta, max_tokens, model)


# ── prompts ────────────────────────────────────────────────────────────────

def _sub_rules() -> str:
    from .narrator import _RULES
    # sub-agents skip the "## Summary" closer — the synthesizer writes it once
    return "\n".join(ln for ln in _RULES.splitlines() if "## Summary" not in ln)


def _subagent_prompt(question: str, agent: dict, cands: list[dict], rmap: str,
                     repo: str, n: int) -> str:
    from .narrator import _numbered
    ids = ", ".join(f"[[{k}]]" for k in agent["chunks"])
    blocks = []
    for k in agent["chunks"]:
        c = cands[k]
        head = f'[{k}] {c["file"]} L{c["start_line"]}-{c["end_line"]}' + \
               (f' ({c["name"]})' if c["name"] else "")
        blocks.append(f'{head}\n{_numbered(c)}')
    return f"""You are sub-agent {agent["id"] + 1} of {n} ("{agent["label"]}") in a team explaining the repo `{repo}`. A synthesizer will MERGE your answer with the other agents' answers, so cover ONLY your assigned slice — no introduction, no overall summary, no repeating the question; go straight into your part of the flow.

REPO MAP (the whole repository, for orientation — your slice is only part of it):
{rmap}

STRICT RULES:
{_sub_rules()}
- You may cite ONLY your own chunks: {ids}. If a tool result shows other useful code, describe it in prose and name the file:lines — do NOT invent a citation for it.
- If you truly need code you don't have, call a tool (search the repo again, fetch a file or symbol). Use at most {TOOL_ROUNDS} tool calls, then answer.

YOUR SUB-QUESTION: {agent["sub_query"]}
(the developer's full question, for context: {question})

YOUR CHUNKS:

{chr(10).join(blocks)}"""


def _synth_body(question: str, partials: list[tuple[dict, str]],
                cands: list[dict], model: str) -> dict:
    from .narrator import _RULES
    idx = "\n".join(_chunk_lines(cands))
    parts = [f'--- sub-agent {a["id"] + 1} · {a["label"]} · "{a["sub_query"]}" ---\n'
             f'{t.strip()}' for a, t in partials]
    prompt = f"""You are the lead engineer merging {len(partials)} partial code walkthroughs (each written by a sub-agent covering one slice of the codebase) into ONE complete, coherent walkthrough that answers the developer's query end to end.

STRICT RULES:
{_RULES}
- The [[k]] citations inside the partials refer to the SHARED chunk index below. Keep the ones you use EXACTLY as written (same numbers) — never renumber, never invent a citation that is not in the index.
- Merge overlaps: if two partials explain the same code, keep the better explanation and cite the span once.

QUERY: {question}

SHARED CHUNK INDEX (number · file · lines · symbol — reference only; the real code gets spliced in at render time):
{idx}

PARTIAL WALKTHROUGHS:

{chr(10).join(parts)}"""
    return {"model": model, "max_tokens": SYNTH_MAX_TOKENS,
            "temperature": 0, "stream": True,
            "messages": [{"role": "user", "content": prompt}]}


# ── orchestrator ───────────────────────────────────────────────────────────

def run_agents(root, question: str, *, res: dict, cands: list[dict], st,
               key: str | None, emit=None, splicer=None, model: str | None = None,
               max_agents: int = MAX_AGENTS) -> dict:
    """Fan out over an ALREADY-RETRIEVED bundle: plan -> parallel sub-agents
    (a ThreadPool over the sub-agents) -> streamed synthesis. Returns
    {"text": raw citation text, "agents": trace, "truncated": bool}; raises
    when no plan is possible or every sub-agent fails (caller fail-opens to
    single-agent ask). `splicer` (ask._Splicer) makes synthesis_delta events
    carry spliced markdown; without it (buffered callers) no synthesis events
    are emitted and the raw text is simply returned."""
    emit = emit or (lambda ev: None)
    model = model or providers.ask_model()
    t0 = time.time()
    rmap = repo_map(st)
    emit({"type": "planning", "model": model, "timeout_s": PLAN_TIMEOUT})
    plan = _plan(question, cands, rmap, key, model, max_agents)
    if not plan or len(plan) < 2:
        raise RuntimeError("no fan-out plan (bundle too narrow)")
    for i, a in enumerate(plan):
        a["id"] = i
    emit({"type": "plan", "ms": int((time.time() - t0) * 1000), "agents": [
        {"id": a["id"], "label": a["label"], "sub_query": a["sub_query"],
         "chunks": [{"k": k, "file": cands[k]["file"],
                     "start_line": cands[k]["start_line"],
                     "end_line": cands[k]["end_line"],
                     "name": cands[k]["name"]} for k in a["chunks"]]}
        for a in plan]})

    tools = _tools(Path(root))
    n = len(plan)
    repo = res["repo"]

    def one(a: dict):
        aid = a["id"]
        files = sorted({cands[k]["file"] for k in a["chunks"]})
        emit({"type": "agent_start", "id": aid, "label": a["label"],
              "sub_query": a["sub_query"], "files": files})
        ta = time.time()
        try:
            text = _agent_llm(_subagent_prompt(question, a, cands, rmap, repo, n),
                              _wrap_tools(tools, emit, aid), key,
                              on_delta=lambda d: emit({"type": "agent_delta",
                                                       "id": aid, "text": d}),
                              max_tokens=SUB_MAX_TOKENS, model=model)
        except Exception as e:  # noqa: BLE001 — one agent down, the rest proceed
            log.debug("sub-agent %s failed", aid, exc_info=True)
            emit({"type": "agent_error", "id": aid, "msg": str(e)})
            return a, None
        emit({"type": "agent_done", "id": aid,
              "ms": int((time.time() - ta) * 1000), "chars": len(text)})
        return a, text

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(one, plan))
    partials = [(a, t) for a, t in results if t]
    if not partials:
        raise RuntimeError("every sub-agent failed")
    trace = [{"id": a["id"], "label": a["label"], "sub_query": a["sub_query"],
              "files": sorted({cands[k]["file"] for k in a["chunks"]}),
              "ok": t is not None} for a, t in results]

    emit({"type": "synthesis_start", "agents": len(partials)})

    def synth_delta(d: str):
        if splicer is not None:
            s = splicer.feed(d)
            if s:
                emit({"type": "synthesis_delta", "text": s})

    truncated = False
    if len(partials) == 1:            # nothing to merge — the partial IS the answer
        text = partials[0][1]
        synth_delta(text)
    else:
        text, stop = providers.stream_chat(_synth_body(question, partials, cands, model),
                                           key, on_delta=synth_delta)
        truncated = stop == "length"
    if splicer is not None:
        s = splicer.flush()
        if s:
            emit({"type": "synthesis_delta", "text": s})
    return {"text": text, "agents": trace, "truncated": truncated}


# ── the unified event driver (CLI / SSE / webui sinks) ─────────────────────

def stream_events(root, question: str, on_event, *, agents: bool | None = None,
                  show_map: bool = True,
                  docs_only: bool = False, include_docs: bool = False,
                  path_filter: str | None = None, state=None,
                  model: str | None = None) -> dict:
    """Run the whole ask flow (retrieval -> classify -> fan-out or single
    agent -> splice) emitting events to on_event. `agents`: None = auto (fan
    out only when classify_bundle says broad), True = force, False = never.
    on_event is called under a lock (sub-agents emit from worker threads).
    Returns the ask()-shaped summary dict for buffered reuse."""
    from .narrator import _build_body, _candidates, _flow_ctx, _Splicer
    lock = threading.Lock()

    def emit(ev: dict):
        with lock:
            on_event(ev)

    t0 = time.time()
    model = model or providers.ask_model()
    st = state or load_state(Path(root))
    # code-only ask (the default): keep docs OUT of the ranking so a doc named
    # like the query + its translations can't crowd the code out of the bundle
    # (graphify's 'how it works' returned 12 docs, 0 code -> fail-open dump).
    # Fail-open: a docs-only repo (no code survived) re-runs with docs in.
    code_only = not docs_only and not include_docs
    res = search_with_state(st, question, path_filter=path_filter,
                            exclude_docs=code_only)
    if code_only and not res["tier1"]:
        res = search_with_state(st, question, path_filter=path_filter)
    retrieval_ms = int((time.time() - t0) * 1000)
    cands = _candidates(res, docs_only, include_docs)
    key = providers.find_chat_key(required=False)
    base = {"result": res, "cands": cands, "query": question, "repo": res["repo"],
            "retrieval_ms": retrieval_ms, "agents": None, "file_syms": {}}

    # SERVE-FROM-CACHE (flow mode only — a no-op when off, the default): a
    # near-exact cached flow whose cited files are still byte-identical is
    # returned verbatim — no LLM, instant, zero cost. Lives HERE, in the one
    # pipeline, so every surface (CLI stream, SSE, MCP, library ask()) gets the
    # same behavior from the single retrieval above.
    if not docs_only and not include_docs:
        from ..storage.flows import serve_verbatim
        served = serve_verbatim(root, res.get("flows") or [])
        if served:
            emit({"type": "cached", "repo": res["repo"], "ms": retrieval_ms,
                  "question": served["question"], "text": served["text"]})
            # `done` is the stream's terminator on EVERY path — a sink must not
            # have to know which branch answered to know the answer ended. The
            # studio papered over this one by fabricating a done client-side
            # for `cached`; a CLI or third-party consumer just hung.
            emit({"type": "done", "spans": served["text"].count("```") // 2,
                  "files": len(served["files"]), "cached": True,
                  "retrieval_ms": retrieval_ms, "llm_ms": 0,
                  "dropped": [], "n_dropped": 0, "agents": None})
            return {**base, "text": served["text"], "llm_ms": 0,
                    "served_from_cache": True}

    emit({"type": "retrieval", "repo": res["repo"], "ms": retrieval_ms,
          "files": len(res["tier1"]) + len(res["tier2"]),
          "model": model, "llm": bool(key and cands),
          # which cached flows ATTACHED as KNOWN-FLOW context (surfaced so the
          # UI can show the cache working; empty when none matched / cache off)
          "flows": [{"question": f["question"], "score": f["score"]}
                    for f in res.get("flows") or []]})
    if not key or not cands:
        emit({"type": "bundle", "note": None, "text": render(res)})
        return {**base, "text": "", "llm_ms": 0}

    file_syms = {f: st.store.symbols_for(f) for f in {c["file"] for c in cands}}
    base["file_syms"] = file_syms
    cls = {"broad": False, "reasons": []}
    if agents is not False:
        cls = classify_bundle(res, question)
        emit({"type": "classified", "broad": cls["broad"],
              "reasons": cls["reasons"], "forced": agents is True})

    splicer = _Splicer(cands, file_syms)
    text, trace, truncated = "", None, False
    t1 = time.time()
    if agents is True or (agents is None and cls["broad"]):
        try:
            o = run_agents(root, question, res=res, cands=cands, st=st, key=key,
                           emit=emit, splicer=splicer, model=model)
            text, trace, truncated = o["text"], o["agents"], o["truncated"]
        except Exception as e:  # noqa: BLE001 — fail-open to single-agent
            log.debug("fan-out failed; single-agent fallback", exc_info=True)
            emit({"type": "error", "stage": "agents",
                  "msg": f"fan-out unavailable ({e}) — single-agent fallback"})
            splicer = _Splicer(cands, file_syms)
    if not text:
        emit({"type": "synthesis_start", "agents": 0})
        stop = ""

        def od(d: str):
            s = splicer.feed(d)
            if s:
                emit({"type": "synthesis_delta", "text": s})

        try:
            # KNOWN-FLOW context for the narrator (non-citable) — the buffered
            # ask always passed it; the unified pipeline keeps that behavior.
            text, stop = providers.stream_chat(
                _build_body(question, cands, _flow_ctx(res), model), key, on_delta=od)
        except Exception:
            log.debug("ask stream interrupted", exc_info=True)
        s = splicer.flush()
        if s:
            emit({"type": "synthesis_delta", "text": s})
        truncated = stop == "length"
    llm_ms = int((time.time() - t1) * 1000)

    if not splicer.cited:              # fail-open: ungrounded prose -> the bundle
        note = "no code cited" if text else "explanation unavailable"
        emit({"type": "bundle", "note": note, "text": render(res)})
        # `done` ENDS the stream for every sink, so it must fire on this path
        # too — without it a UI that keys "finished" off the event streams
        # forever (the studio sat on "SYNTHESIS · STREAMING", no footer, on
        # every ungrounded answer). `grounded` lets a sink say WHY the
        # walkthrough carries no code instead of showing bare prose.
        emit({"type": "done", "spans": 0, "files": 0, "grounded": False,
              "retrieval_ms": retrieval_ms, "llm_ms": llm_ms,
              "dropped": [], "n_dropped": 0, "agents": trace})
        return {**base, "text": text, "llm_ms": llm_ms, "truncated": truncated}
    if truncated:
        emit({"type": "length"})
    dropped = [f'{c["file"].rsplit("/", 1)[-1]}:{c["start_line"]}'
               for i, c in enumerate(cands) if i not in splicer.cited]
    emit({"type": "done", "spans": len(splicer.seen), "files": splicer.files,
          "retrieval_ms": retrieval_ms, "llm_ms": llm_ms,
          "dropped": dropped[:12] if show_map else [],
          "n_dropped": len(dropped), "agents": trace})
    out = {**base, "text": text, "llm_ms": llm_ms, "agents": trace,
           "truncated": truncated}
    # WRITE PATH of the flow cache: a successful cited walkthrough is a
    # workflow worth remembering. Lives HERE, in the one pipeline, so EVERY
    # surface accumulates flows (the old buffered-only write meant CLI/SSE
    # asks never populated the cache). cache_flow gates on enabled(root) and
    # is fail-open by construction; reuses st.emb.
    from .narrator import _SEL, _splice, cited_files
    if text and _SEL.search(text):
        from ..storage.flows import cache_flow
        body, _, _ = _splice(out)
        cache_flow(Path(root), question, body, cited_files(out),
                   emb=getattr(st, "emb", None))
    return out
