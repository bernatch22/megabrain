"""query.py — the stable retrieval import surface (compatibility facade).

The implementation was split by responsibility (art-of-code layering):

    state    SearchState + load_state (warm per-repo state, lifecycle)
    scoring  score_chunks + the path/test/ident helpers (single scoring truth)
    bundle   search_with_state / search / search_multi / selection /
             chunks_for_file / prune_search (rank + tier + project)
    render   lang_of / render / render_pruned (pure view)
    files    get_code (the file-serving security boundary)

Everything anyone imported from `megabrain.retrieval.query` — including the
underscore names used across the engine and tests (_score_chunks,
_ident_tokens, _under_path, _apply_path_filter, _is_test_path) — is re-exported
here unchanged, so `from .retrieval.query import ...` keeps working. New code
may import the concrete modules directly.
"""

from __future__ import annotations

from .bundle import (
    OUTLINE_KINDS,
    chunks_for_file,
    chunks_for_file_root,
    prune_search,
    prune_search_root,
    search,
    search_multi,
    search_with_state,
    selection,
)
from .files import get_code
from .params import DEFAULT_PARAMS, RetrievalParams
from .render import lang_of, render, render_pruned
from .scoring import (
    TEST_DIR_SEGS,
    _apply_path_filter,
    _ident_tokens,
    _is_test_path,
    _under_path,
    ident_tokens,
    score_chunks,
)
from .state import SearchState, load_state

# underscore alias kept: forge_eval + older imports referenced the private name
# before score_chunks was made public.
_score_chunks = score_chunks

__all__ = [
    "SearchState", "load_state", "RetrievalParams", "DEFAULT_PARAMS",
    "score_chunks", "_score_chunks", "ident_tokens", "_ident_tokens",
    "_under_path", "_apply_path_filter", "_is_test_path", "TEST_DIR_SEGS",
    "OUTLINE_KINDS", "search", "search_with_state", "search_multi", "selection",
    "chunks_for_file", "chunks_for_file_root", "prune_search", "prune_search_root",
    "render", "render_pruned", "lang_of", "get_code",
]
