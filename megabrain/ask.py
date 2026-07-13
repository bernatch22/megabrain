"""megabrain ask — agent-style explained answer with cherry-picked REAL code.

The LLM explains the answer like an agent walking through the codebase, but
it cannot paste code: it cites chunks as [[3]] or [[3:705-731]] and the engine
REPLACES each citation with the real code block (file header + fenced code,
true line numbers). Explanation = LLM; every line of code = verbatim from
disk. Streamed, ~1-3s. Fail-open: no citations / API error -> full bundle.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path

from . import providers
from .indexing.strategies import MarkdownStrategy
from .retrieval.query import SearchState, lang_of, load_state, render, search_with_state

log = logging.getLogger(__name__)

# ask is a CODE walkthrough: docs (markdown) are excluded from its candidates so a
# code explanation isn't diluted with prose. docs_only flips it to a docs-only
# walkthrough. Docs stay retrievable via `query` regardless.
DOC_EXTS = MarkdownStrategy.exts

MAX_CTX_CHARS = 200_000  # ~50K tokens of candidate code; fits every default model
# double-bracket so the model can still mention [n] in prose without collision.
# Tolerate an "L" prefix and stray spaces on the line range: the chunk headers in
# the prompt read "L1-172", so the model often mirrors that as [[0:L1-172]] — accept
# it (and [[3:705-731]], [[3]]) instead of leaking the citation as raw text.
_SEL = re.compile(r"\[\[(\d+)(?::\s*[Ll]?(\d+)\s*-\s*[Ll]?(\d+))?\s*\]\]")


def _candidates(res: dict, docs_only: bool = False,
                include_docs: bool = False) -> list[dict]:
    """Retrieved chunks for the walkthrough: CORE chunks + RELATED best chunks,
    numbered. Three modes: default = code only (citing doc prose pollutes a code
    walkthrough), docs_only = docs-only walkthrough, include_docs = code AND
    docs together. `query` surfaces both regardless of this setting."""
    def keep(f: str) -> bool:
        is_doc = f.endswith(DOC_EXTS)
        if docs_only:
            return is_doc
        return True if include_docs else not is_doc
    out = []
    for t in res["tier1"]:
        if not keep(t["file"]):
            continue
        for c in t["chunks"]:
            out.append({"file": t["file"], **{k: c[k] for k in
                        ("name", "kind", "start_line", "end_line", "text")}})
    for t in res["tier2"]:
        if not keep(t["file"]):
            continue
        bc = t.get("best_chunk")
        if bc:
            out.append({"file": t["file"], **{k: bc[k] for k in
                        ("name", "kind", "start_line", "end_line", "text")}})
    return out


_RULES = """- NEVER paste or quote code. Cite it with DOUBLE brackets: [[3]] (whole chunk) or [[3:705-731]] (file lines 705-731 of chunk 3). Each such citation is REPLACED by the real code block in your answer, so explain AROUND the code, not the code itself. (If you ever need to mention the citation syntax itself in prose, use single brackets — only [[...]] gets replaced.)
- Every chunk line is prefixed with its ABSOLUTE file line number ("1234| code"). For a [[k:lo-hi]] sub-range, read lo and hi OFF those prefixes — never count or estimate lines yourself.
- A sub-range must be a COMPLETE unit: lo = the line of the enclosing def/function/method signature (include a comment block directly above it), hi = its final closing line. Never start or stop mid-function, and never begin a citation on another function's trailing lines.
- Put each [[...]] citation on its own line, right after the sentence that introduces it.
- Show GENEROUS, COMPLETE code: cite whole [[k]] chunks (a full function/class/block) by default so the reader sees the complete implementation, not a fragment. Only use a [[k:lo-hi]] sub-range when a chunk is very large and only one section is relevant — and then take the WHOLE enclosing function, not a few lines. Never cite the same span twice.
- Structure it: use ## section headings for each phase of the flow, 1-3 sentences of explanation per citation. Be thorough — the reader must understand everything perfectly from the code shown, without opening any file.
- Finish the thought: end with a short "## Summary" of the flow in 2-3 sentences. Never end mid-sentence."""


def _flow_ctx(res: dict) -> str:
    """Cached-flow context for the narrator: a previously synthesized walkthrough
    of a matching workflow. Explicitly non-citable — the model can only cite the
    numbered code chunks, so a stale flow can mis-prioritize but never fabricate."""
    flows = res.get("flows") or []
    if not flows:
        return ""
    from .flows import strip_code
    parts = [f'(cached from: "{f["question"]}")\n{strip_code(f["text"])}' for f in flows]
    return ("\nKNOWN FLOW — a walkthrough of this workflow synthesized by a "
            "previous ask over the SAME code (context only; do NOT cite it — "
            "cite only the numbered chunks below):\n\n"
            + "\n\n---\n\n".join(parts) + "\n")


def _numbered(c: dict) -> str:
    """Chunk text with each line prefixed by its ABSOLUTE file line number —
    the model reads sub-range bounds off these instead of counting lines
    itself (unnumbered text made [[k:lo-hi]] cites land a few lines off,
    cutting functions mid-body). Prompt-only: splicing uses the clean text."""
    s = c["start_line"]
    return "".join(f"{s + i}| {ln}"
                   for i, ln in enumerate(c["text"].splitlines(keepends=True)))


def _build_body(question: str, cands: list[dict], flow_ctx: str = "") -> dict:
    """Chat request body (OpenAI schema): the cite-only walkthrough prompt over numbered chunks."""
    blocks, used = [], 0
    for i, c in enumerate(cands):
        head = f'[{i}] {c["file"]} L{c["start_line"]}-{c["end_line"]}' + \
               (f' ({c["name"]})' if c["name"] else "")
        body = _numbered(c)
        if used + len(body) > MAX_CTX_CHARS:
            body = body[:2000] + "\n# ...truncated...\n"
        used += len(body)
        blocks.append(f"{head}\n{body}")
    prompt = f"""You are a senior engineer giving a complete code walkthrough that answers the developer's query. Cover the ENTIRE relevant flow end to end — do not stop early, do not leave a thread dangling.

STRICT RULES:
{_RULES}

QUERY: {question}
{flow_ctx}
RETRIEVED CHUNKS:

{chr(10).join(blocks)}"""
    return {"model": providers.ask_model(), "max_tokens": 2400, "temperature": 0,
            "stream": True, "messages": [{"role": "user", "content": prompt}]}


def _explain_stream(question: str, cands: list[dict], key: str,
                    flow_ctx: str = "") -> str:
    """ONE streamed chat call -> explanation text with [[k]]/[[k:lo-hi]] citations."""
    text, stop = providers.stream_chat(_build_body(question, cands, flow_ctx), key)
    if stop == "length":
        cut = max(text.rfind("\n\n"), text.rfind(". "))
        if cut > 0:
            text = text[:cut + 1].rstrip() + "\n\n_(walkthrough truncated — ask a narrower question for the rest)_"
    return text


def _code_block(c: dict, lo: int | None, hi: int | None, seen: set,
                file_syms: dict[str, list[dict]]) -> str:
    cs, ce = c["start_line"], c["end_line"]
    s, e = cs, ce
    if lo is not None and hi is not None and not (hi < cs or lo > ce):
        s, e = max(lo, cs), min(hi, ce)
    _FN = ("function", "async_function", "method", "async_method", "class")
    syms = [y for y in file_syms.get(c["file"], []) if y["kind"] in _FN]
    if (s, e) != (cs, ce):
        # snap to enclosing symbol edges when close (readable boundaries)
        encl = [y for y in syms if y["line"] <= e and y["end_line"] >= s]
        if encl:
            best = min(encl, key=lambda y: y["end_line"] - y["line"])
            if 0 < s - best["line"] <= 8:
                s = max(best["line"], cs)
            if 0 < best["end_line"] - e <= 8:
                e = min(best["end_line"], ce)
        # trim orphan tail of a previous symbol at the head of the range
        nexts = sorted(y["line"] for y in syms if s < y["line"] <= min(s + 8, e))
        if nexts:
            owner = [y for y in syms if y["line"] < s <= y["end_line"]
                     and y["end_line"] < nexts[0]]
            if owner:
                s = nexts[0]
    lines = c["text"].splitlines(keepends=True)
    text = "".join(lines[s - cs:e - cs + 1])
    key = (c["file"], s, e)
    if key in seen:
        return f'*(see `{c["file"]}:L{s}-{e}` above)*'
    seen.add(key)
    # label = most specific symbols overlapping the emitted range
    inside = [y for y in syms if not (y["end_line"] < s or y["line"] > e)]
    inside.sort(key=lambda y: y["end_line"] - y["line"])
    tight = [y for y in inside if (y["end_line"] - y["line"]) <= 3 * (e - s + 1)]
    label = ", ".join(dict.fromkeys(y["name"] for y in (tight or inside)[:2])) \
        or (c["name"] or c["kind"])
    return (f'\n**`{c["file"]}` L{s}-{e}** — {label}\n'
            f'```{lang_of(c["file"])}\n{text.rstrip(chr(10))}\n```\n')


# A trailing PREFIX of a possible citation ("[", "[[3", "[[3:L1-"): the splicer
# holds only this back, so prose streams token-by-token while a citation split
# across deltas never leaks raw. Anchored to $ and bracket/digit-only, so any
# intervening prose breaks the match.
_PARTIAL = re.compile(r"\[(?:\[(?:\d+(?::\s*[Ll]?\d*(?:-\s*[Ll]?\d*)?)?\]?)?)?$")


class _Splicer:
    """Incremental [[k]]/[[k:lo-hi]] -> code-block substitution over a token
    stream: emits prose IMMEDIATELY (token-level streaming) and holds back only
    a trailing partial-citation prefix until it completes or turns out to be
    plain text. One instance per answer, shared by the CLI stream, the SSE
    endpoint and ask v2 synthesis so every surface grounds code identically
    (same seen-dedupe, same cited set)."""

    def __init__(self, cands: list[dict], file_syms: dict[str, list[dict]]):
        self.cands, self.file_syms = cands, file_syms
        self.seen: set = set()
        self.cited: set = set()
        self._pending = ""

    def _sub(self, m):
        k = int(m.group(1))
        if not (0 <= k < len(self.cands)):
            return m.group(0)
        self.cited.add(k)
        lo = int(m.group(2)) if m.group(2) else None
        hi = int(m.group(3)) if m.group(3) else None
        return _code_block(self.cands[k], lo, hi, self.seen, self.file_syms)

    def feed(self, d: str) -> str:
        self._pending += d
        m = _PARTIAL.search(self._pending)
        cut = m.start() if m else len(self._pending)
        if cut == 0:
            return ""
        ready, self._pending = self._pending[:cut], self._pending[cut:]
        return _SEL.sub(self._sub, ready)

    def flush(self) -> str:
        ready, self._pending = self._pending, ""
        return _SEL.sub(self._sub, ready) if ready else ""

    @property
    def files(self) -> int:
        return len({self.cands[k]["file"] for k in self.cited})


def ask(root: Path, question: str, rerank: bool = False,
        docs_only: bool = False, path_filter: str | None = None,
        state: SearchState | None = None, include_docs: bool = False,
        agents: bool | None = False) -> dict:
    """One-shot ask. `agents`: False = always single-agent (the default, so
    library callers and evals keep v1 behavior and cost), None = AUTO (fan out
    into parallel sub-agents only when ask_agents.classify_bundle says the
    question is broad), True = force the fan-out. Frontends (CLI/MCP/HTTP)
    pass auto. Fan-out is fail-open: any error falls back to the single-agent
    call, then to the bundle."""
    t0 = time.time()
    st = state or load_state(Path(root))
    res = search_with_state(st, question, rerank=rerank, path_filter=path_filter)
    retrieval_ms = int((time.time() - t0) * 1000)
    cands = _candidates(res, docs_only, include_docs)
    key = providers.find_chat_key(required=False)
    text, llm_ms, trace = "", 0, None

    # SERVE-FROM-CACHE: a near-exact cached flow whose code is still current is
    # returned verbatim — no LLM, instant, zero cost. (Only when not docs-mode;
    # the sha recheck in serve_verbatim keeps it from ever serving stale code.)
    if not docs_only and not include_docs:
        from .flows import serve_verbatim
        served = serve_verbatim(root, res.get("flows") or [])
        if served:
            return {"result": res, "cands": cands, "text": served["text"],
                    "file_syms": {}, "retrieval_ms": retrieval_ms, "llm_ms": 0,
                    "served_from_cache": True, "query": question,
                    "repo": res["repo"], "agents": None}

    if key and cands:
        t1 = time.time()
        if agents is not False:
            try:
                from .ask_agents import classify_bundle, run_agents
                if agents is True or classify_bundle(res, question)["broad"]:
                    o = run_agents(root, question, res=res, cands=cands,
                                   st=st, key=key)
                    text, trace = o["text"], o["agents"]
            except Exception:
                log.debug("multi-agent ask failed (falling back to single-agent)",
                          exc_info=True)
        if not text:
            try:
                text = _explain_stream(question, cands, key, _flow_ctx(res))
            except Exception:
                log.debug("ask explanation failed (falling back to full bundle)",
                          exc_info=True)
                text = ""
        llm_ms = int((time.time() - t1) * 1000)
    file_syms = {f: st.store.symbols_for(f) for f in {c["file"] for c in cands}}
    out = {"result": res, "cands": cands, "text": text, "file_syms": file_syms,
           "retrieval_ms": retrieval_ms, "llm_ms": llm_ms,
           "query": question, "repo": res["repo"], "agents": trace}
    # WRITE PATH of the flow cache: a successful cited walkthrough is a workflow
    # worth remembering. We cache the SPLICED BODY (prose + real code from disk,
    # no header/footer) so a near-exact later question can be served verbatim
    # without an LLM. Fail-open by construction; reuses st.emb.
    if text and _SEL.search(text):
        from .flows import cache_flow
        body, _, _ = _splice(out)
        cache_flow(Path(root), question, body, cited_files(out), emb=st.emb)
    return out


def cited_files(out: dict) -> list[str]:
    """Files cited in the explanation, in first-mention order (for eval)."""
    cands = out["cands"]
    files: list[str] = []
    for m in _SEL.finditer(out["text"] or ""):
        k = int(m.group(1))
        if 0 <= k < len(cands):
            f = cands[k]["file"]
            if f not in files:
                files.append(f)
    return files


def _splice(out: dict) -> tuple[str, set, set]:
    """Replace every [[k]]/[[k:lo-hi]] citation in out["text"] with its verbatim
    code block. Returns (body, seen spans, cited candidate indices). The body is
    also what the flow cache stores — prose + real code, no header/footer."""
    cands, text = out["cands"], out["text"]
    seen: set = set()
    cited: set = set()

    def sub(m):
        k = int(m.group(1))
        if not (0 <= k < len(cands)):
            return m.group(0)
        cited.add(k)
        lo = int(m.group(2)) if m.group(2) else None
        hi = int(m.group(3)) if m.group(3) else None
        return _code_block(cands[k], lo, hi, seen, out.get("file_syms", {}))

    return _SEL.sub(sub, text).strip(), seen, cited


def render_ask(out: dict) -> str:
    text = out["text"]
    if out.get("served_from_cache"):
        # already a fully rendered body (cached from a previous splice) — wrap
        # it in a fresh header; never fall through to the citation path.
        return (f'# megabrain — "{out["query"]}"\n'
                f'repo `{out["repo"]}` · ⚡ served from flow cache · '
                f'{out["retrieval_ms"]}ms retrieval + 0ms explain\n\n{text}')
    if not text or not _SEL.search(text):
        return render(out["result"])  # fail-open: unfiltered bundle
    cands = out["cands"]
    body, seen, cited = _splice(out)
    n_files = len({cands[k]["file"] for k in cited})
    L = [f'# megabrain — "{out["query"]}"',
         f'repo `{out["repo"]}` · {len(seen)} code spans · {n_files} files · '
         f'{out["retrieval_ms"]}ms retrieval + {out["llm_ms"]}ms explain\n',
         body]
    dropped = [c for i, c in enumerate(cands) if i not in cited]
    if dropped:
        items = ", ".join(f'{c["file"].rsplit("/", 1)[-1]}:{c["start_line"]}'
                          for c in dropped[:12])
        L.append(f'\n— not cited ({len(dropped)}): {items}')
        L.append('— full bundle: `megabrain query` · any file: `megabrain get <file>`')
    return "\n".join(L)


def stream_ask(root: Path, question: str, out=None, rerank: bool = False,
               show_map: bool = True, docs_only: bool = False,
               path_filter: str | None = None, include_docs: bool = False,
               agents: bool | None = None) -> None:
    """Live-streaming `ask` for the terminal — a sink over
    ask_agents.stream_events: prose appears token by token, each
    [[k]]/[[k:lo-hi]] citation is spliced into its real code block as its line
    completes, and broad questions print the fan-out plan + per-agent progress
    as status lines while the sub-agents run in parallel (their prose stays
    off the terminal — multiplexed streams are noise; the synthesis streams).
    Same grounding + fail-open as render_ask. Programmatic/eval/MCP callers
    keep using ask()/render_ask()."""
    import json as _json

    from .ask_agents import stream_events
    out = out or sys.stdout

    def write(s: str):
        out.write(s)
        out.flush()

    # SERVE-FROM-CACHE (flow mode only — a no-op when off, the default): a
    # near-exact cached flow whose code is still byte-identical prints instantly
    # with no LLM. Costs one retrieval; on a miss, stream_events re-retrieves
    # (accepted: the mode is opt-in and the hit saves a whole LLM call).
    from .flows import enabled as _flows_on
    if not docs_only and not include_docs and _flows_on(root):
        try:
            from .flows import serve_verbatim
            from .retrieval.query import load_state, search_with_state
            _st = load_state(Path(root))
            _res = search_with_state(_st, question, path_filter=path_filter)
            served = serve_verbatim(root, _res.get("flows") or [])
            if served:
                write(f'# megabrain — "{question}"\n')
                write(f'repo `{_res["repo"]}` · ⚡ served from flow cache '
                      f'(cached ask: "{served["question"][:70]}") · 0ms explain\n\n')
                write(served["text"].rstrip() + "\n")
                return
        except Exception:
            log.debug("flow serve check failed; streaming normally", exc_info=True)

    def sink(ev: dict):
        t = ev["type"]
        if t == "retrieval":
            if ev["llm"]:              # no LLM/candidates -> bare bundle, no header
                write(f'# megabrain — "{question}"\n')
                write(f'repo `{ev["repo"]}` · {ev["ms"]}ms retrieval · '
                      f'streaming {ev["model"]}…\n\n')
        elif t == "classified":
            if ev["broad"] or ev["forced"]:
                why = "; ".join(ev["reasons"]) or "forced"
                write(f'◆ broad question ({why}) — fanning out…\n')
        elif t == "planning":
            write(f'◆ planner ({ev["model"]}) splitting the bundle…\n')
        elif t == "plan":
            for a in ev["agents"]:
                write(f'◆ agent {a["id"] + 1}/{len(ev["agents"])} `{a["label"]}` — '
                      f'"{a["sub_query"]}" · {len(a["chunks"])} chunks\n')
        elif t == "agent_tool":
            write(f'  ⌕ agent {ev["id"] + 1} → {ev["tool"]} '
                  f'{_json.dumps(ev["args"])[:90]}\n')
        elif t == "agent_done":
            write(f'✓ agent {ev["id"] + 1} done ({ev["ms"] / 1000:.1f}s)\n')
        elif t == "agent_error":
            write(f'✗ agent {ev["id"] + 1} failed: {ev["msg"]}\n')
        elif t == "synthesis_start":
            if ev["agents"]:
                write('— synthesizing…\n\n')
        elif t == "synthesis_delta":
            write(ev["text"])
        elif t == "length":
            write("\n\n_(walkthrough truncated — ask a narrower question for the rest)_")
        elif t == "error":
            write(f'✗ {ev["msg"]}\n')
        elif t == "bundle":            # fail-open: ungrounded/no-LLM -> the bundle
            if ev["note"]:
                write(f'\n\n_({ev["note"]} — full bundle below)_\n\n')
            write(ev["text"] + "\n")
        elif t == "done":
            write(f'\n\n— {ev["spans"]} code spans · {ev["files"]} files · '
                  f'{ev["retrieval_ms"]}ms retrieval + {ev["llm_ms"]}ms explain\n')
            if ev["dropped"]:
                write(f'— not cited ({ev["n_dropped"]}): {", ".join(ev["dropped"])}\n')
                write('— full bundle: `megabrain query` · any file: '
                      '`megabrain get <file>`\n')

    stream_events(Path(root), question, sink, agents=agents, rerank=rerank,
                  show_map=show_map, docs_only=docs_only,
                  include_docs=include_docs, path_filter=path_filter)
