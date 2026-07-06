"""megabrain — code-intelligence engine: one-shot retrieval of all code related
to a feature, as a view-ready map.

Public API (lazy — numpy/tree_sitter load on first use, not at import):

    index_repo(root)                    build/update a repo index (incremental)
    search(root, query)                 no-LLM retrieval -> bundle dict
    render(result)                      bundle dict -> markdown code map
    get_code(root, relpath, symbol=None)  one file / one symbol
    load_state(root)                    warm retrieval state (long-running apps)
    search_with_state(state, query)     query against a warm state
    Store(root)                         low-level SQLite index access

For the LLM walkthrough import the module: `from megabrain.ask import ask,
render_ask, stream_ask` (kept off the top level so the `ask` submodule and the
function never shadow each other).

Validated configuration (experiments phases 0-5, June 2026):
- chunking: cAST split-then-merge, 4000 nws chars, breadcrumb headers
- embeddings: pplx-embed-v1-0.6b via OpenRouter (1024d, int8 wire format, L2-normalized)
- scoring: dense chunk cosine + 0.5 * file-skeleton cosine
- graph: import+call edges; used for bundle candidates and map annotations,
  NOT for ranking (PageRank rejected by experiment)
- pruning: OFF by default (LLM pruning costs completeness); --prune optional
"""

from importlib import import_module

__version__ = "0.3.2"

_EXPORTS = {
    "index_repo": ".indexer",
    "search": ".query",
    "render": ".query",
    "get_code": ".query",
    "load_state": ".query",
    "search_with_state": ".query",
    "Store": ".store",
}
__all__ = [*_EXPORTS, "__version__"]


def __getattr__(name: str):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module 'megabrain' has no attribute {name!r}")
    return getattr(import_module(mod, __name__), name)


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
