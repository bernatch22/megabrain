# megabrain — agent orientation

## What this project is

megabrain is a local **code-intelligence engine**. One call returns all the code related to a question, explained with the real code spliced in. It exists to replace minutes of file-by-file crawling (grep + Read + explore agents) with one grounded answer. Overview: [README.md](README.md).

Pipeline: `index` (cAST chunk → pplx embed → SQLite, incremental by sha256) → `query` (no-LLM retrieval: dense chunk + file-skeleton fusion + graph candidates) → `ask` (one Haiku call narrates and cites `[[k]]`; the engine replaces each citation with verbatim code — the model cannot rewrite code; streamed live to the terminal; **code-only by default**, `--docs` for a docs-only walkthrough).

## Using it (dogfood — prefer this over crawling files)

This engine answers questions about any indexed repo, including itself:
```bash
megabrain index ~/megabrain        # once; incremental after
megabrain ask   ~/megabrain "how does ask splice real code"
```
Via MCP, `megabrain_ask` / `megabrain_query` are registered — use them instead of grep/Read chains for code research.

## Hard rules (locked by experimental data — do not violate)

1. **No LLM in the retrieval/query path.** Pruning with an LLM was rejected (phase 5: cost completeness). The only LLM calls are `ask` (post-retrieval explainer) and `--best` (optional reorder) — both fail-open.
2. **Completeness beats ordering.** Never merge a change that lowers golden `bundle_full` (currently **1.00**).
3. **Graph never ranks.** Import/call edges supply candidates + map annotations only (PageRank-as-ranking rejected: Acc@1 0.91→0.73).
4. **Chunks are a line partition.** `validate_partition` must stay clean — no gaps, no overlaps.
5. **`ask` shows real code only.** The LLM cites `[[k]]`/`[[k:lo-hi]]`; the engine splices verbatim from disk. Never let the model emit code.

## After ANY change to `megabrain/`, run the gates

```bash
python3 -m pytest tests/test_cast_chunker.py tests/test_chunker_ts.py tests/test_markdown_chunker.py tests/test_ask_citation.py -q
```
Current bar: 37 unit tests green (chunker + markdown + ask-citation). The end-to-end retrieval
benchmark (R@1 0.86 · bundle_full 1.00 · p50 ~8ms) runs against a local indexed corpus and is
kept out of this repo.

## Module map

`chunker.py` Python cAST · `chunker_ts.py` generic `TreeSitterChunker` + `LangSpec` (TS/JS, Ruby, Go) · `markdown.py` no-LLM QMD-style doc chunker · `strategies.py` ext→strategy registry · `embeddings.py` pplx (int8, L2-norm) · `store.py` SQLite · `graph.py` import/call edges · `indexer.py` registry-driven incremental walk · `query.py` fusion + bundle + render · `issue.py` deterministic issue parsing (traceback grounding, variant ensemble) · `bm25.py` sparse entity lane · `rerank.py`/`rerank2.py` optional Haiku reorder · `ask.py` explanation with spliced code · `cli.py` · `mcp_server.py`.

## What's next

Priority 1 (chunking-strategy registry) is
**done**: a `strategies.py` maps extension → chunk strategy, so the indexer is content-
agnostic. Indexed today: `.py` · `.ts/.tsx/.js/.jsx/.mjs/.cjs` (TS grammar, JS-superset) ·
Ruby `.rb` · Go `.go` (optional — `pip install tree_sitter_ruby tree_sitter_go`) ·
markdown `.md/.markdown/.mdx` (no-LLM QMD-style scored chunking). Adding a language or
content type is now a registry entry, not a branch in the indexer. Remaining is Priority 2
(embedding-provider abstraction, `.tsx` arrow-component symbols, packaging, SWE-bench eval).

Keys: `PERPLEXITY_API_KEY` (required), `ANTHROPIC_API_KEY` (ask/--best only) — env or `~/.zshrc` fallback. Repo: github.com/pinecall/megabrain (branch `best-mode` = current stack).
