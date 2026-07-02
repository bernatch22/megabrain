"""megabrain — code-intelligence engine: one-shot retrieval of all code related
to a feature, as a view-ready map.

Validated configuration (experiments phases 0-5, June 2026):
- chunking: cAST split-then-merge, 4000 nws chars, breadcrumb headers
- embeddings: pplx-embed-v1-0.6b via OpenRouter (1024d, int8 wire format, L2-normalized)
- scoring: dense chunk cosine + 0.5 * file-skeleton cosine
- graph: import+call edges; used for bundle candidates and map annotations,
  NOT for ranking (PageRank rejected by experiment)
- pruning: OFF by default (LLM pruning costs completeness); --prune optional
"""

__version__ = "0.3.0"
