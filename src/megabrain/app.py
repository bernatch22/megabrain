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
          reindex: bool = True) -> dict:
    """No-LLM noise pruning -> flat ranked signal chunks."""
    from .retrieval.bundle import prune_search_root
    _maybe_reindex(root, reindex)
    return prune_search_root(root, task, path_filter=path_filter,
                             with_text=with_text, include_pruned=include_pruned)


def ask(root: Path, question: str, path_filter: str | None = None,
        docs_only: bool = False, include_docs: bool = False,
        agents: Any = None, reindex: bool = True) -> dict:
    """Buffered ask (MCP / POST /ask). `agents` accepts the raw transport value;
    normalized here. Returns the ask() out dict (render with ask.render_ask)."""
    from .ask import ask as _ask
    _maybe_reindex(root, reindex)
    return _ask(root, question, docs_only=docs_only, include_docs=include_docs,
                path_filter=path_filter, agents=normalize_agents(agents))


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


def index(root: Path, force: bool = False, exclude=()) -> dict:
    """Incremental index/update — returns stats; the caller renders them."""
    from .indexing.indexer import index_repo
    return index_repo(root, force=force, exclude=exclude)


def stats(root: Path) -> dict:
    """Index shape counts (no raw SQL in the frontend — Store owns it)."""
    from .storage.store import Store
    with Store(root) as s:
        return s.stats()
