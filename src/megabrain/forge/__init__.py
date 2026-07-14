"""megabrain forge — chunking strategies the machine writes or measures.

    coverage    the COVERAGE forge: detect uncovered file types, LLM-generate a
                ChunkStrategy per type, accept only after the partition oracle
                passes on EVERY matching file, install trust-gated
    ab_gate     the empirical judge: champion-vs-challenger on neutral probes
                over throwaway indexed copies (rank-aware IoU + hit@k, with the
                anti-micro-chunking teeth)
    specialize  hand-written specialization strategies, measured by ab_gate and
                installed only on a WIN (the LLM path was removed — it lost)

Package interface: `from megabrain.forge import forge, detect, render_report`.
"""

from .coverage import detect, forge, install, render_report, validate_strategy

__all__ = ["forge", "detect", "install", "render_report", "validate_strategy"]
