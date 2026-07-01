"""megabrain ask — agent-style explained answer with cherry-picked REAL code.

The LLM explains the answer like an agent walking through the codebase, but
it cannot paste code: it cites chunks as [[3]] or [[3:705-731]] and the engine
REPLACES each citation with the real code block (file header + fenced code,
true line numbers). Explanation = LLM; every line of code = verbatim from
disk. Streamed, ~1-3s. Fail-open: no citations / API error -> full bundle.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from . import providers
from .query import lang_of, render, search
from .strategies import MarkdownStrategy

# ask is a CODE walkthrough: docs (markdown) are excluded from its candidates so a
# code explanation isn't diluted with prose. docs_only flips it to a docs-only
# walkthrough. Docs stay retrievable via `query` regardless.
DOC_EXTS = MarkdownStrategy.exts

MODEL = providers.ASK_MODEL
MAX_CTX_CHARS = 200_000  # ~50K tokens of candidate code; Haiku window is 200K
# double-bracket so the model can still mention [n] in prose without collision.
# Tolerate an "L" prefix and stray spaces on the line range: the chunk headers in
# the prompt read "L1-172", so the model often mirrors that as [[0:L1-172]] — accept
# it (and [[3:705-731]], [[3]]) instead of leaking the citation as raw text.
_SEL = re.compile(r"\[\[(\d+)(?::\s*[Ll]?(\d+)\s*-\s*[Ll]?(\d+))?\s*\]\]")


def _candidates(res: dict, docs_only: bool = False) -> list[dict]:
    """Retrieved chunks for the walkthrough: CORE chunks + RELATED best chunks,
    numbered. By default docs (markdown) are excluded — ask is a code walkthrough and
    citing doc prose pollutes it. docs_only=True flips it to a docs-only walkthrough.
    `query` surfaces both regardless of this setting."""
    def keep(f: str) -> bool:
        is_doc = f.endswith(DOC_EXTS)
        return is_doc if docs_only else not is_doc
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
- Put each [[...]] citation on its own line, right after the sentence that introduces it.
- Show GENEROUS, COMPLETE code: cite whole [[k]] chunks (a full function/class/block) by default so the reader sees the complete implementation, not a fragment. Only use a [[k:lo-hi]] sub-range when a chunk is very large and only one section is relevant — and then take the WHOLE enclosing function, not a few lines. Never cite the same span twice.
- Structure it: use ## section headings for each phase of the flow, 1-3 sentences of explanation per citation. Be thorough — the reader must understand everything perfectly from the code shown, without opening any file.
- Finish the thought: end with a short "## Summary" of the flow in 2-3 sentences. Never end mid-sentence."""


def _build_body(question: str, cands: list[dict]) -> dict:
    """Chat request body (OpenAI schema): the cite-only walkthrough prompt over numbered chunks."""
    blocks, used = [], 0
    for i, c in enumerate(cands):
        head = f'[{i}] {c["file"]} L{c["start_line"]}-{c["end_line"]}' + \
               (f' ({c["name"]})' if c["name"] else "")
        body = c["text"]
        if used + len(body) > MAX_CTX_CHARS:
            body = body[:2000] + "\n# ...truncated...\n"
        used += len(body)
        blocks.append(f"{head}\n{body}")
    prompt = f"""You are a senior engineer giving a complete code walkthrough that answers the developer's query. Cover the ENTIRE relevant flow end to end — do not stop early, do not leave a thread dangling.

STRICT RULES:
{_RULES}

QUERY: {question}

RETRIEVED CHUNKS:

{chr(10).join(blocks)}"""
    return {"model": MODEL, "max_tokens": 2400, "temperature": 0, "stream": True,
            "messages": [{"role": "user", "content": prompt}]}


def _explain_stream(question: str, cands: list[dict], key: str) -> str:
    """ONE streamed chat call -> explanation text with [[k]]/[[k:lo-hi]] citations."""
    text, stop = providers.stream_chat(_build_body(question, cands), key)
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


def ask(root: Path, question: str, rerank: bool = False,
        docs_only: bool = False) -> dict:
    t0 = time.time()
    res = search(Path(root), question, rerank=rerank)
    retrieval_ms = int((time.time() - t0) * 1000)
    cands = _candidates(res, docs_only)
    key = providers.find_key(required=False)
    text, llm_ms = "", 0
    if key and cands:
        t1 = time.time()
        try:
            text = _explain_stream(question, cands, key)
        except Exception:
            text = ""
        llm_ms = int((time.time() - t1) * 1000)
    from .store import Store
    st = Store(Path(root))
    file_syms = {f: st.symbols_for(f) for f in {c["file"] for c in cands}}
    return {"result": res, "cands": cands, "text": text, "file_syms": file_syms,
            "retrieval_ms": retrieval_ms, "llm_ms": llm_ms,
            "query": question, "repo": res["repo"]}


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


def render_ask(out: dict) -> str:
    cands, text = out["cands"], out["text"]
    if not text or not _SEL.search(text):
        return render(out["result"])  # fail-open: unfiltered bundle
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

    body = _SEL.sub(sub, text).strip()
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
               show_map: bool = True, docs_only: bool = False) -> None:
    """Live-streaming `ask` for the terminal: prose appears token by token and each
    [[k]]/[[k:lo-hi]] citation is spliced into its real code block as soon as its line
    completes (citations are emitted on their own line). Same grounding + fail-open as
    render_ask, but the reader sees output immediately instead of waiting for the whole
    walkthrough. Programmatic/eval/MCP callers keep using ask()/render_ask()."""
    out = out or sys.stdout

    def write(s: str):
        out.write(s)
        out.flush()

    t0 = time.time()
    res = search(Path(root), question, rerank=rerank)
    retrieval_ms = int((time.time() - t0) * 1000)
    cands = _candidates(res, docs_only)
    key = providers.find_key(required=False)
    if not key or not cands:           # no LLM available / nothing retrieved
        write(render(res) + "\n")
        return

    from .store import Store
    st = Store(Path(root))
    file_syms = {f: st.symbols_for(f) for f in {c["file"] for c in cands}}

    write(f'# megabrain — "{question}"\n')
    write(f'repo `{res["repo"]}` · {retrieval_ms}ms retrieval · streaming {MODEL}…\n\n')

    seen: set = set()
    cited: set = set()

    def sub(m):
        k = int(m.group(1))
        if not (0 <= k < len(cands)):
            return m.group(0)
        cited.add(k)
        lo = int(m.group(2)) if m.group(2) else None
        hi = int(m.group(3)) if m.group(3) else None
        return _code_block(cands[k], lo, hi, seen, file_syms)

    pending = [""]  # hold the in-progress line; citations live on their own line

    def on_delta(d: str):
        pending[0] += d
        nl = pending[0].rfind("\n")
        if nl != -1:
            ready, pending[0] = pending[0][:nl + 1], pending[0][nl + 1:]
            write(_SEL.sub(sub, ready))

    t1 = time.time()
    interrupted = False
    stop = ""
    try:
        _, stop = providers.stream_chat(_build_body(question, cands), key, on_delta=on_delta)
    except Exception:
        interrupted = True
    if pending[0]:                     # flush the trailing partial line
        write(_SEL.sub(sub, pending[0]))
        pending[0] = ""
    llm_ms = int((time.time() - t1) * 1000)

    if not cited:                      # fail-open: ungrounded prose -> show the bundle
        note = "_(explanation unavailable — full bundle below)_" if interrupted \
            else "_(no code cited — full bundle below)_"
        write(f"\n\n{note}\n\n{render(res)}\n")
        return
    if stop == "length":
        write("\n\n_(walkthrough truncated — ask a narrower question for the rest)_")

    n_files = len({cands[k]["file"] for k in cited})
    write(f'\n\n— {len(seen)} code spans · {n_files} files · '
          f'{retrieval_ms}ms retrieval + {llm_ms}ms explain\n')
    if show_map:
        dropped = [c for i, c in enumerate(cands) if i not in cited]
        if dropped:
            items = ", ".join(f'{c["file"].rsplit("/", 1)[-1]}:{c["start_line"]}'
                              for c in dropped[:12])
            write(f'— not cited ({len(dropped)}): {items}\n')
            write('— full bundle: `megabrain query` · any file: `megabrain get <file>`\n')
