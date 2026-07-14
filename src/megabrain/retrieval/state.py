"""Warm per-repo retrieval state.

SearchState holds everything a query needs preloaded — the Store handle, the
chunk + file embedding matrices, and the lazily-built issue/flow lanes — so a
long-running server pays the SQLite matrix load once and every query hits warm
memory. Lifecycle: build with load_state(); one-shot callers (search(),
prune_search_root(), ...) close via `with`; servers close on state reload or
shutdown. The frozen RetrievalParams travel WITH the state, so sweeps inject a
tuning variant instead of monkeypatching module globals.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..providers.embeddings import Embedder
from ..storage.store import Store
from .params import DEFAULT_PARAMS, RetrievalParams


@dataclass
class SearchState:
    """Preloaded, reusable retrieval state for one repo. Build once with
    load_state(); a long-running server (serve.py) keeps it warm so each query
    skips the SQLite matrix load. CLI/MCP go through search(), which builds it
    per call — identical results, just not cached."""
    store: Store
    emb: Embedder
    metas: list
    M: np.ndarray
    fpaths: list
    fskels: list
    F: np.ndarray
    repo: str
    # issue-mode lanes, built lazily on the first long query and cached — a
    # warm server would otherwise rebuild BM25 + the symbol corpus per query.
    bm25: object | None = None
    issue_files: list | None = None
    issue_syms: list | None = None
    # flow cache (flows.py): cached ask syntheses + their matrix, and the last
    # query vector (score_chunks stashes it so the flow lane re-uses the one
    # embed call — retrieval never embeds twice, never calls an LLM).
    flows: list | None = None
    FL: np.ndarray | None = None
    FLQ: np.ndarray | None = None
    qv: np.ndarray | None = None
    # every tuning knob, injectable (sweeps replace() this instead of
    # monkeypatching module globals). Frozen -> safe to share across threads.
    params: RetrievalParams = DEFAULT_PARAMS

    def close(self) -> None:
        """Release the underlying SQLite connection. One-shot entries
        (search/prune_search_root/…) close via `with`; long-running servers
        close on state reload/shutdown."""
        self.store.close()

    def __enter__(self) -> "SearchState":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_state(root: Path, check_same_thread: bool = True,
               params: RetrievalParams | None = None) -> SearchState:
    """Load the per-repo retrieval state (chunk + file matrices) once. The
    expensive part of a query — kept out of the hot path by serve.py.
    `params` injects a tuning variant (default: the validated configuration)."""
    store = Store(Path(root), check_same_thread=check_same_thread)
    metas, M = store.load_matrix()
    fpaths, fskels, F = store.load_file_matrix()
    repo = store.get_meta("repo_name") or Path(root).name
    # flow cache is opt-in and OFF by default: unless the mode is on for this
    # repo, flows stay empty and the read path below is a pure no-op — plain
    # query/ask behave exactly as before, at zero cost.
    from ..storage.flows import enabled as _flows_on
    flows, FL, FLQ = store.load_flows() if _flows_on(root) else ([], None, None)
    return SearchState(store, Embedder(), metas, M, fpaths, fskels, F, repo,
                       flows=flows, FL=FL, FLQ=FLQ,
                       params=params or DEFAULT_PARAMS)
