"""app — the application-service layer: one use-case per verb.

The three frontends (CLI, MCP, serve-api) used to each hand-roll the same
pre-steps: resolve a repo root from a possibly-sub-path, join a bare filename
onto a scope, normalize the agents tri-state, decide whether to auto-reindex.
That produced 4 copies of the rel-join fallback, 3 different agents parsings,
and an inconsistent reindex policy. Those pre-steps live HERE now; a frontend
maps its transport args to a use-case call and renders the result — nothing
more. Each use-case preserves the behavior of the strictest current caller;
where the frontends diverged, the docstring says which behavior won.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ── shared pre-steps (the de-duplicated logic) ─────────────────────────────

def resolve_scope(path: str | Path, scope_path: str | None = None) -> tuple[Path, str | None]:
    """(repo_root, path_filter) for PATH-SCOPE. `path` may be a sub-path inside
    an indexed repo; an explicit `scope_path` is appended to it. path_filter is
    None at the root. Raises IndexNotFound when no index is found up the tree."""
    from .storage.store import resolve_root
    p = Path(path).expanduser()
    sub = (scope_path or "").strip().strip("/")
    if sub:
        p = p / sub
    root, subpath = resolve_root(p)
    return root, (subpath or None)


def rel_join(root: Path, sub: str | None, rel: str) -> str:
    """THE one copy of the 'bare file under a sub-path' fallback (was pasted 4×
    across CLI/MCP get+chunks): `megabrain get ~/repo/src dispatch.ts` finds
    src/dispatch.ts when the bare name doesn't exist at the root but does under
    the scope."""
    if sub and not (Path(root) / rel).exists() and (Path(root) / sub / rel).exists():
        return (Path(sub) / rel).as_posix()
    return rel


def normalize_agents(value: Any) -> bool | None:
    """The multi-agent tri-state, parsed ONE way (was 3): None or "auto" ->
    None (AUTO: fan out only when the question is broad); anything else -> its
    truthiness (True forces the fan-out, False forbids it)."""
    if value is None or value == "auto":
        return None
    return bool(value)


def _maybe_reindex(root: Path, reindex: bool) -> None:
    if reindex:
        from .indexing.indexer import maybe_reindex
        maybe_reindex(root)          # answers match disk (60s TTL, fail-open)


# ── use-cases (one per verb; thin — the engine does the work) ───────────────

def query(root: Path, task: str, path_filter: str | None = None,
          reindex: bool = True) -> dict:
    """Single-repo retrieval bundle."""
    from .retrieval.bundle import search
    _maybe_reindex(root, reindex)
    return search(root, task, path_filter=path_filter)


def query_multi(roots: list[Path], task: str,
                path_filters: list[str | None] | None = None,
                reindex: bool = True) -> dict:
    """Cross-repo retrieval (CLI comma-separated paths)."""
    from .retrieval.bundle import search_multi
    if reindex:
        for r in dict.fromkeys(roots):
            _maybe_reindex(r, True)
    return search_multi(roots, task, path_filters=path_filters)


def prune(root: Path, task: str, path_filter: str | None = None,
          with_text: bool = True, include_pruned: bool = False,
          reindex: bool = True, llm_rerank: bool = False) -> dict:
    """No-LLM noise pruning -> flat ranked signal chunks. `llm_rerank` adds the
    opt-in LLM lane on top (drop vocabulary-only matches, reorder); it fails
    open to the deterministic result, so the floor never drops."""
    from .retrieval.bundle import prune_search_root
    _maybe_reindex(root, reindex)
    res = prune_search_root(root, task, path_filter=path_filter,
                            with_text=with_text, include_pruned=include_pruned)
    if llm_rerank:
        from .retrieval.rerank import llm_rerank as _rerank
        res = _rerank(res, task)
    return res


def ask(root: Path, question: str, path_filter: str | None = None,
        docs_only: bool = False, include_docs: bool = False,
        agents: Any = None, reindex: bool = True,
        model: str | None = None) -> dict:
    """Buffered ask (MCP / POST /ask). `agents` accepts the raw transport value;
    normalized here. `model` overrides the narrator model for this call (the UI
    model picker); None = the provider default. Returns the ask() out dict
    (render with ask.render_ask)."""
    from .ask import ask as _ask
    _maybe_reindex(root, reindex)
    return _ask(root, question, docs_only=docs_only, include_docs=include_docs,
                path_filter=path_filter, agents=normalize_agents(agents),
                model=model)


def graph(root: Path, mode: str = "map", node: str | None = None,
          source: str | None = None, target: str | None = None,
          path_filter: str | None = None, reindex: bool = True,
          label: bool = True) -> dict:
    """Knowledge-graph views over the indexed repo: map (communities + god
    nodes + surprises), node (one file: neighbors + real code), path (BFS
    between two concepts, endpoints resolved by embedding). `label=False`
    skips the (cached, fail-open) LLM community labels."""
    from .graph import graph_root
    _maybe_reindex(root, reindex)
    return graph_root(root, mode=mode, node=node, source=source,
                      target=target, path_filter=path_filter, label=label)


def get(root: Path, sub: str | None, file: str, symbol: str | None = None) -> str:
    """One file or symbol. Owns resolve+rel_join so a bare name under a scope
    resolves. NOTE: get does NOT auto-reindex — faithful to today's callers
    (CLI/MCP get skip the refresh; a raw file read doesn't need a fresh index)."""
    from .retrieval.files import get_code
    return get_code(root, rel_join(root, sub, file), symbol)


def chunks(root: Path, sub: str | None, file: str, query_str: str,
           path_filter: str | None = None, reindex: bool = True) -> dict:
    """Every chunk of one file, scored + selected flags."""
    from .retrieval.bundle import chunks_for_file_root
    _maybe_reindex(root, reindex)
    return chunks_for_file_root(root, rel_join(root, sub, file), query_str,
                                path_filter=path_filter)


def index(root: Path, force: bool = False, exclude=(),
          scan_filters: bool = False) -> dict:
    """Incremental index/update — returns stats; the caller renders them.
    `scan_filters` (opt-in) honors .gitignore + skips vendored/generated."""
    from .indexing.indexer import index_repo
    return index_repo(root, force=force, exclude=exclude, scan_filters=scan_filters)


def scan(root: Path) -> dict:
    """Index-intelligence census (no indexing): what WOULD index + every
    skipped candidate with its reason. Powers `megabrain scan` and GET /scan."""
    from .indexing.ignore import scan as _scan_census
    from .indexing.indexer import EXCLUDE_DIRS, load_ignore
    from .indexing.strategies import all_exts, build_registry, load_repo_strategies
    root = Path(root).resolve()
    reg = build_registry(root.name, extra=tuple(load_repo_strategies(root, root.name)))
    return _scan_census(root, all_exts(reg), exclude=load_ignore(root),
                        extra_names=EXCLUDE_DIRS)


def stats(root: Path) -> dict:
    """Index shape counts (no raw SQL in the frontend — Store owns it)."""
    from .storage.store import Store
    with Store(root) as s:
        return s.stats()


def flows_list(root: Path) -> dict:
    """The flow cache as a listing: every cached ask (id, question, cited
    files, when, size) WITHOUT the stored text — the light shape for a list
    view. `stale` marks flows whose cited files changed since caching (they
    survive until the next index prunes them)."""
    from .storage.flows import enabled, files_current
    from .storage.store import Store
    with Store(root) as s:
        metas, _, _ = s.load_flows()
    # staleness vs DISK, the same check the serve path makes — NOT the index's
    # shas (Store.stale_flows), which can lag disk by the 60 s refresh TTL and
    # would flag a perfectly serveable flow as doomed.
    return {"enabled": enabled(root),
            "flows": [{"id": m["id"], "question": m["question"],
                       "files": sorted(m["files"]), "created": m["created"],
                       "chars": len(m["text"]),
                       "stale": not files_current(root, m["files"])}
                      for m in reversed(metas)]}      # newest first


def flow_get(root: Path, flow_id: int) -> dict:
    """One cached flow in full — the stored walkthrough (prose + real code
    spliced at cache time) for the viewer."""
    from .errors import MegabrainError
    from .storage.store import Store
    with Store(root) as s:
        for m in s.load_flows()[0]:
            if m["id"] == flow_id:
                return {"id": m["id"], "question": m["question"],
                        "files": sorted(m["files"]), "created": m["created"],
                        "text": m["text"]}
    e = MegabrainError(f"no cached flow with id {flow_id}")
    e.http_status = 404
    raise e


def flow_delete(root: Path, flow_id: int) -> dict:
    from .storage.store import Store
    with Store(root) as s:
        s.delete_flow(flow_id)
        s.commit()
    return {"deleted": flow_id}


def example_queries(root: Path, limit: int = 6) -> dict:
    """Starter questions for the studio's Ask chips — EVERY indexed repo gets
    some, so a newcomer never faces a blank box. Three tiers, best first:

      "file"    <root>/.megabrainqueries — the repo AUTHORED its main
                workflows (one per line, `#` comments). Committed intent wins.
      "flows"   the questions already in the flow cache. These are the best
                fallback by far: each one's answer is CACHED, so clicking the
                chip serves instantly with no LLM and no rate-limit cost.
      "derived" deterministic, no-LLM questions over the repo's central files
                (see ask.warmup.derive_questions) — always something.

    `source` tells the UI which tier it got so it can label the row honestly
    (an already-answered question is worth advertising as instant)."""
    from .ask.warmup import authored_questions, derive_questions
    authored = authored_questions(root)
    if authored:
        return {"source": "file", "queries": authored[:limit]}
    try:
        from .storage.store import Store
        with Store(Path(root)) as s:
            cached = [m["question"] for m in reversed(s.load_flows()[0])]
        if cached:
            return {"source": "flows", "queries": cached[:limit]}
    except Exception:                       # noqa: BLE001 — never break the chips
        pass
    return {"source": "derived", "queries": derive_questions(root, limit)}
