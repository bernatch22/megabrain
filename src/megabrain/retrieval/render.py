"""Rendering: a retrieval bundle (or pruned result) -> view-ready markdown.

Pure view layer — takes the plain dicts search_with_state()/prune_search()
return and formats them; no Store, no scoring, no I/O. RELATED renders as a
MAP by default (file + best-match span + symbols, no code bodies): measured on
the golden set, RELATED holds 45% of the gold files but ~95% of its VOLUME is
non-gold code that flooded agent context windows — `related_code=True`
restores the old inline render for callers that want it.
"""

from __future__ import annotations


def lang_of(path: str) -> str:
    return {"py": "python", "ts": "typescript", "tsx": "tsx", "js": "javascript",
            "jsx": "jsx", "mjs": "javascript", "cjs": "javascript", "rb": "ruby",
            "go": "go", "php": "php", "md": "markdown", "markdown": "markdown",
            "mdx": "markdown"}.get(path.rsplit(".", 1)[-1], "")


# symbol kinds worth surfacing in the file outline (display only — not ranking).
# Spans Python, TS/JS, Ruby/Go and doc headings so every content type shows.
# Mirrors bundle.OUTLINE_KINDS; kept here too so render filters the rest-of-file
# outline without importing the assembly layer.
OUTLINE_KINDS = ("class", "function", "async_function", "method", "async_method",
                 "constant", "const", "var", "interface", "type", "enum",
                 "module", "heading")


def render(res: dict, compact: bool = False, related_code: bool = False) -> str:
    """Bundle dict -> view-ready markdown map.

    RELATED renders as a MAP by default — file, best-match span pointer,
    symbols — without chunk code bodies. Measured on the golden set, RELATED
    holds 45% of the gold files (it cannot be dropped) but ~95% of its VOLUME
    is non-gold code bodies that flooded agent context windows (~16K of a
    ~22K-token bundle). The bundle DATA is unchanged (ask/serve consume
    best_chunk as before); `related_code=True` (CLI/MCP: full) restores the
    old inline-code render."""
    L: list[str] = []
    n1, n2 = len(res["tier1"]), len(res["tier2"])
    L.append(f'# megabrain — "{res["query"]}"')
    L.append(f'repo `{res["repo"]}` · {n1} core files (full code) · {n2} related (mapped) · {res["ms"]}ms\n')

    # cached flows first: a previously-synthesized walkthrough of this very
    # workflow is the highest-density context in the bundle. Clearly labeled as
    # a cached synthesis — the code truth stays in CORE/RELATED below.
    for fl in res.get("flows", []):
        L.append(f'## KNOWN FLOW (cached ask) — "{fl["question"]}"  `{fl["score"]:.2f}`')
        L.append(f'sources: {", ".join(fl["files"])}\n')
        if not compact:
            L.append(fl["text"].rstrip())
        L.append("")

    L.append("## CORE\n")
    for i, t in enumerate(res["tier1"], 1):
        L.append(f'### {i}. {t["file"]}  `{t["score"]:.2f}`')
        if t["neighbors"]:
            L.append(f'linked: {", ".join(t["neighbors"])}')
        covered = []
        for c in t["chunks"]:
            covered.append((c["start_line"], c["end_line"]))
            part = f' (part {c["part"]})' if c["part"] else ""
            L.append(f'\n**{c["name"] or c["kind"]}** L{c["start_line"]}-{c["end_line"]}{part}')
            if not compact:
                L.append(f'```{lang_of(t["file"])}')
                L.append(c["text"].rstrip("\n"))
                L.append("```")
        rest = [s for s in t["symbols"]
                if not any(lo <= s["line"] <= hi for lo, hi in covered)
                and s["kind"] in OUTLINE_KINDS]
        if rest:
            L.append("\nrest of file:")
            for s in rest[:20]:
                d = f' — {s["doc"]}' if s["doc"] else ""
                L.append(f'- `{s["signature"]}` L{s["line"]}{d}')
        L.append("")

    if res["tier2"]:
        hint = "" if related_code else " · code bodies: `--full`"
        L.append("## RELATED — best match + symbols per file · expand with "
                 f"`megabrain get <file> [--symbol NAME]`{hint}\n")
        for t in res["tier2"]:
            via = " ·via-graph" if t["via_graph"] else (
                " ·via-flow" if t.get("via_flow") else (
                    " ·via-floor" if t.get("via_floor") else ""))
            match = f' · matched: {", ".join(t["matched"])}' if t["matched"] else ""
            doc = f' — {t["doc"]}' if t["doc"] else ""
            L.append(f'### {t["file"]}  `{t["score"]:.2f}`{via}{match}{doc}')
            bc = t.get("best_chunk")
            if bc and not compact:
                L.append(f'**{bc["name"] or bc["kind"]}** L{bc["start_line"]}-{bc["end_line"]}')
                if related_code:
                    L.append(f'```{lang_of(t["file"])}')
                    L.append(bc["text"].rstrip("\n"))
                    L.append("```")
            for s in t["symbols"][:6]:
                L.append(f'- `{s["signature"]}` L{s["line"]}-{s["end_line"]}')
            L.append("")
    return "\n".join(L)


# Output budget for the pruned render, in characters. The agent's context is
# the scarce resource, and MCP hosts persist oversized tool results to a FILE
# the agent then never opens — a 90KB "answer" is an unread answer (field
# case: click#3362 run, one scoped question rendered 90KB deterministic /
# 60KB post-rerank, overflowed the host's inline limit, and the agent saw a
# 2KB preview — then did its own Reads anyway, scoring the tool 6/10). The
# budget degrades BODIES to span pointers, never drops files: completeness
# is chunk-list completeness, and every omitted body says exactly what to
# Read instead.
RENDER_BUDGET = 24_000
# Below this remaining budget a partial window isn't worth rendering — emit
# the span pointer instead of a five-line fragment.
MIN_WINDOW_CHARS = 800


def _query_window(lines: list[str], query: str, char_budget: int) -> tuple[int, int]:
    """The largest line window that fits `char_budget`, grown outward from the
    line sharing the most identifier characters with the query (head when
    nothing matches). Used ONLY when a body does not fit the remaining budget
    — a body that fits is NEVER cut (field report, verbatim: 'me truncó justo
    lo que necesitaba… el costo de devolverlo entero era trivial'; the old
    unconditional 80-line cap fired with the budget nowhere near spent, and
    the agent had to Read both files anyway)."""
    from .rerank import _IDENT, _score_line
    qtok = {t.lower() for t in _IDENT.findall(query)}
    scores = [_score_line(ln, qtok) for ln in lines]
    best = max(range(len(lines)), key=scores.__getitem__) if any(scores) else 0
    lo = hi = best
    used = len(lines[best]) + 1
    while True:
        grew = False
        if lo > 0 and used + len(lines[lo - 1]) + 1 <= char_budget:
            lo -= 1
            used += len(lines[lo]) + 1
            grew = True
        if hi < len(lines) - 1 and used + len(lines[hi + 1]) + 1 <= char_budget:
            hi += 1
            used += len(lines[hi]) + 1
            grew = True
        if not grew:
            return lo, hi + 1


def _numbered(lines: list[str], first: int) -> list[str]:
    """Prefix each line with its true line number and the `→` gutter that
    megabrain_read uses — one gutter format across both tools."""
    return [f'{first + i:>6}→{ln}' for i, ln in enumerate(lines)]


def render_pruned(res: dict, with_text: bool = True,
                  budget: int | None = None) -> str:
    """Pruned result -> ranked markdown list: `[id] file L… (name) · score`,
    each with its code (unless with_text=False), spent top-down against a
    character budget. A body that fits the remaining budget renders WHOLE —
    never cut; one that doesn't renders the query-centered window that does
    fit, or a span pointer when the leftover is too small to be worth it."""
    import os
    if budget is None:
        budget = int(os.environ.get("MEGABRAIN_RENDER_BUDGET",
                                    str(RENDER_BUDGET)))
    L: list[str] = []
    L.append(f'# megabrain search — "{res["query"]}"')
    rr = res.get("reranked")
    tail = (f' · reranked by `{rr["model"]}` (+{rr["ms"]}ms, '
            f'dropped {rr["dropped"]} tangential)' if rr else "")
    # The spec tests are announced HERE, not only listed at the bottom: after a
    # few hundred lines of code bodies, a two-line compact tail is invisible —
    # the feature's own author looked at an output that contained it and asked
    # where the tests were. The header is the one line everyone reads.
    n_tests = len(res.get("tests") or [])
    spec = f' · ⚠ {n_tests} spec test(s) at the BOTTOM' if n_tests else ""
    L.append(f'repo `{res["repo"]}` · {res["kept"]} signal chunks '
             f'({res["pruned"]} pruned as noise){spec} · {res["ms"]}ms{tail}\n')
    spent = 0
    omitted = 0
    for rank, c in enumerate(res["chunks"], 1):
        label = c["name"] or c["kind"]
        from .rerank import _is_reexport_chunk
        retag = " · re-exports" if _is_reexport_chunk(c.get("text") or "") else ""
        L.append(f'### {rank}. [{c["id"]}] {c["file"]} '
                 f'L{c["start_line"]}-{c["end_line"]} · {label} · '
                 f'`{c["score"]:.3f}`{retag}')
        text = c.get("text") if with_text else None
        if text:
            lines = text.rstrip("\n").splitlines()
            body = "\n".join(lines)
            remaining = budget - spent
            # true line numbers per line (same `N→` gutter as megabrain_read),
            # so a search result feeds megabrain_replace directly: the reader
            # sees exactly where each line lives, and the `→` marks the prefix
            # to strip when building a find string. Budget is spent on the RAW
            # body (the gutter is presentation, not the disk text).
            if len(body) <= remaining:
                # fits -> WHOLE, always. No per-chunk cap exists anymore.
                L.append(f'```{lang_of(c["file"])}')
                L.append("\n".join(_numbered(lines, c["start_line"])))
                L.append("```")
                spent += len(body)
            elif remaining >= MIN_WINDOW_CHARS:
                # doesn't fit -> the query-centered window that does
                start, end = _query_window(lines, res.get("query", ""),
                                           remaining)
                # omitted-side pointers speak megabrain_read's spec format
                # (file:start-end) so the agent pastes them into ONE batched
                # read instead of host-Reading files one per turn.
                shown = _numbered(lines[start:end], c["start_line"] + start)
                if start:
                    shown.insert(0, f'… {start} lines above — megabrain_read '
                                    f'{c["file"]}:{c["start_line"]}-'
                                    f'{c["start_line"] + start - 1}')
                if end < len(lines):
                    shown.append(f'… +{len(lines) - end} lines — '
                                 f'megabrain_read '
                                 f'{c["file"]}:{c["start_line"] + end}-'
                                 f'{c["end_line"]}')
                window = "\n".join(shown)
                L.append(f'```{lang_of(c["file"])}')
                L.append(window)
                L.append("```")
                spent += len(window)
            else:
                omitted += 1
                L.append(f'(body omitted — output budget · megabrain_read '
                         f'{c["file"]}:{c["start_line"]}-{c["end_line"]})')
        L.append("")
    if omitted:
        L.insert(2, f'⚠ output budget {budget // 1000}K: {omitted} lower-ranked '
                    f'bodies rendered as span pointers — every span is still '
                    f'listed; batch the pointed specs in ONE megabrain_read.\n')
    # Tests the rerank kept OUT of the signal list. Compact (no bodies): they
    # crowd implementation by shared vocabulary, but they are the SPEC of the
    # behavior — changing the mechanism above means reading them.
    if res.get("tests"):
        L.append("— tests pinning this behavior (read before changing it):")
        for c in res["tests"]:
            L.append(f'  [{c["id"]}] {c["file"]} L{c["start_line"]}-{c["end_line"]}'
                     + (f' · {c["name"]}' if c.get("name") else ""))
        L.append("")
    # The audit trail: "N pruned as noise" asks for faith unless the pruned
    # spans are visible (field report: "unfalsifiable from inside a single
    # call"). Top spans only, one line each, rerank drops flagged — a glance
    # tells the caller whether anything relevant was cut, a Read expands it.
    nm = res.get("noise_map") or []
    if nm:
        shown = nm[:10]
        L.append(f'— pruned, auditable (top {len(shown)} of {len(nm)} by score):')
        for c in shown:
            flag = " · dropped by rerank" if c.get("rerank_drop") else ""
            L.append(f'  {c["file"]} L{c["start_line"]}-{c["end_line"]} '
                     f'· `{c["score"]}`{flag}')
        L.append("")
    return "\n".join(L)
