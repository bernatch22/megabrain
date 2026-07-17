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
                " ·via-flow" if t.get("via_flow") else "")
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


def render_pruned(res: dict, with_text: bool = True) -> str:
    """Pruned result -> ranked markdown list: `[id] file L… (name) · score`,
    each with its code (unless with_text=False). Noise dropped, signal only."""
    L: list[str] = []
    L.append(f'# megabrain search — "{res["query"]}"')
    rr = res.get("reranked")
    tail = (f' · reranked by `{rr["model"]}` (+{rr["ms"]}ms, '
            f'dropped {rr["dropped"]} tangential)' if rr else "")
    L.append(f'repo `{res["repo"]}` · {res["kept"]} signal chunks '
             f'({res["pruned"]} pruned as noise) · {res["ms"]}ms{tail}\n')
    for rank, c in enumerate(res["chunks"], 1):
        label = c["name"] or c["kind"]
        L.append(f'### {rank}. [{c["id"]}] {c["file"]} '
                 f'L{c["start_line"]}-{c["end_line"]} · {label} · `{c["score"]:.3f}`')
        if with_text and c.get("text"):
            L.append(f'```{lang_of(c["file"])}')
            L.append(c["text"].rstrip("\n"))
            L.append("```")
        L.append("")
    return "\n".join(L)
