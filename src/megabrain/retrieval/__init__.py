"""Answering queries — no LLM in this package (locked rule #1).

Layered by responsibility — import the concrete module you need:

    state    warm per-repo SearchState + load_state (lifecycle)
    scoring  score_chunks + path/test/ident helpers — the one scoring truth
    bundle   rank + tier into CORE/RELATED; selection/prune/chunks_for_file
    render   bundle -> markdown (pure view)
    files    get_code — the file-serving containment boundary
    issue    deterministic issue/traceback parsing (no LLM)
    bm25     the sparse entity-ID lane (issue mode only)
    rerank   optional permute-only LLM reorder (--best)
"""
