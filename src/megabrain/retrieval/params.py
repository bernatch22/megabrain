"""RetrievalParams — every tuning knob of the retrieval pipeline, in ONE
frozen record, with its measurement provenance on the field.

These defaults ARE the validated configuration: each number was grid-tuned or
A/B-measured on the golden set (see field comments; history in CHANGELOG /
AGENTS.md). Changing any default is a RANKING-SHIFTING change — it needs a
golden-gate run by policy.

Injection replaces module-global mutation: sweeps (forge_eval grids, future
classifier evals) construct a variant with dataclasses.replace() and pass it
to load_state(root, params=...) instead of monkeypatching query.py globals.
The default path is byte-identical to the pre-params engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalParams:
    # ── fusion / ranking lanes ──────────────────────────────────────────
    file_fusion_w: float = 0.5    # dense + w·file-skeleton cosine (phase 3 winner)
    test_penalty: float = 0.85    # soft down-weight for test files in ranking
    file_boost_w: float = 0.05    # per matched filename token (capped at 2; grid-tuned p6)
    sym_boost_w: float = 0.03     # per matched symbol-name token (capped at 2; grid-tuned p6)
    lexical_boost_cap: int = 2    # token-match cap for both boosts

    # ── issue mode (long queries: bug reports / tracebacks) ─────────────
    issue_token_threshold: int = 25   # ident tokens above this flip issue mode
                                      # (NEXT.md: "reasoned, not tuned" — the
                                      # one admitted-untested heuristic)
    rrf_k: int = 60                   # reciprocal-rank-fusion constant
    tier_bonus: tuple[float, float, float] = (0.6, 0.25, 0.10)  # grounding pins t0/t1/t2
    span_bonus: float = 0.15          # chunk overlaps a pinned traceback span

    # ── bundle assembly ─────────────────────────────────────────────────
    tier1_max: int = 4
    tier1_gap: float = 0.97       # full code only within 3% of top score (noise control)
    cand_files: int = 12
    graph_extras: int = 7         # neighbors of top files pulled into tier2
                                  # (recall-safe; retuned 6->7 after the
                                  # edge-preservation fix: +35% edges compete)
    chunk_keep_ratio: float = 0.8  # within a tier-1 file, keep chunks >= ratio*best
    tier1_chunk_cap: int = 12      # hard cap of chunks per CORE file
    rerank_deep_pool: int = 24     # candidate pool when the LLM rerank runs
    multi_tier1_extra: int = 2     # search_multi: tier1 cap = tier1_max + this


DEFAULT_PARAMS = RetrievalParams()
