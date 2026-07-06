# Changelog

## 0.4.0 — unreleased

Open-source readiness release. Retrieval behavior is unchanged where it counts:
all three retrieval gates hold the locked bar (golden R@1 0.86 · bundle_full
1.00 · scale p50 < 20 ms).

### Fixed
- **Windows: indexes were corrupt** — relpaths were stored with `\` while the
  whole engine matches on `/` (DB keys, excludes, path filters, graph edges,
  `chunks`/`get` lookups), so nothing resolved. Relpaths are now POSIX on every
  platform. (Caught by the new Windows CI matrix.)

### Security
- `get_code` now enforces repo-root containment — `../` and absolute paths can
  no longer escape the index root (was reachable via `serve-api GET /get` and
  MCP `megabrain_get`).
- `serve-api` gained optional Bearer auth: `--token` / `MEGABRAIN_API_TOKEN`
  guards every endpoint except `/health`; a warning is printed when binding
  beyond localhost without one.

### Changed
- **`query` renders RELATED as a map by default** (file, best-match span,
  symbols — no chunk code bodies; CLI `--full` / MCP `full: true` restores
  them). Measured on the golden set: RELATED holds 45% of the gold files so it
  can't be dropped, but its code bodies were ~16K of a ~22K-token bundle at
  ~5% verified signal — they flooded agent context windows. The bundle DATA is
  unchanged (`ask`/HTTP consumers keep `best_chunk`), all three retrieval
  gates hold (bundle_full 1.00), and a typical bundle drops ~22K → ~8K tokens.
- **Default index excludes trimmed to universal dirs.** `data`, `logs` and
  maintainer-local names are no longer skipped by default — add them to your
  repo's `.megabrainignore` if you relied on that. New defaults add `.tox`,
  `.mypy_cache`, `.ruff_cache`, `target`, `vendor`, `.nuxt`.
- **Chunkers moved to `megabrain.chunkers`** (`base` / `python` / `treesitter`
  / `php` / `markdown`). The old module paths (`megabrain.chunker`,
  `chunker_ts`, `chunker_php`, `markdown`) remain as deprecation shims for one
  release.
- Rerank v1 removed (unused); `rerank2.haiku_order2` is now
  `rerank.llm_order` (the default model has been qwen3-coder since the
  OpenRouter move).
- `/docsearch` result groups are now per-deployment config
  (`.megabrain/docsearch.json` or `MEGABRAIN_DOCSEARCH_GROUPS`) instead of
  hardcoded section names; unmatched slugs group under "Docs".
- CLI: single-path commands now error on comma multi-path input instead of
  silently dropping everything after the first comma.

### Added
- `ask --with-docs` (MCP `include_docs`, HTTP `include_docs`): explain code
  AND docs together — third mode next to the default (code only) and `--docs`
  (docs only).
- CLI `ask`/`query`/`chunks` now auto-refresh a stale index before answering
  (60 s TTL, incremental, fail-open without a key) — previously only the MCP
  server did, so CLI answers could cite stale code after an edit.
- **Claude chat provider** (extra `megabrain[claude]`): `ask`/`--best` stream
  through the Claude Agent SDK — Claude Code **subscription credits** when the
  CLI is logged in, or `ANTHROPIC_API_KEY` for API billing. Default model
  `haiku` (`MEGABRAIN_ASK_MODEL` accepts any Claude model/alias). The chat
  provider **defaults to auto**: Claude when its SDK is importable, else
  OpenRouter — pin with `MEGABRAIN_CHAT_PROVIDER=claude|openrouter`. Embeddings
  are unaffected and still require OpenRouter or a local embed endpoint.
- **Custom chunking strategies**: `index_repo(root, strategies=[MyStrategy()])`
  plugs any content type in without forking (checked before the built-ins, so
  a custom strategy can also override one). New `ChunkStrategy` protocol;
  `Chunk`/`Symbol`/`FileResult`/`validate_partition` exported at top level.
- `examples/`: programmatic API walkthrough, a complete custom `.sql` chunker
  (offline-runnable), and a terminal chunk-score heatmap.
- Lazy public API: `megabrain.{index_repo, search, render, get_code,
  load_state, search_with_state, Store}` (importing `megabrain` no longer
  pulls numpy/tree_sitter) + `py.typed`.
- Issue grounding beyond Python: JS/TS stack frames (`at fn (src/x.ts:12:5)`)
  pin files/spans; explicit `.ts/.tsx/.js/.jsx/.mjs/.cjs/.rb/.go/.rs/.php`
  paths ground like `.py` paths.
- TS import graph: dynamic `import()`, side-effect imports, and
  `.jsx/.mjs/.cjs`/`index.js` resolution.
- `MEGABRAIN_DEBUG=1` surfaces previously-swallowed provider errors.

### Performance
- `ask` loads retrieval state once per question (was: matrices twice + an
  extra SQLite connection) and accepts a warm `state`.
- BM25 scores via postings (only docs containing each term).
- Issue-mode lanes (BM25 + symbol grounding corpus) cached on `SearchState`
  for warm servers.
- `search_multi` queries repos concurrently; embedding cache writes are
  atomic (safe under concurrency).

## 0.3.2
- Legacy-PHP section chunker (banner sections, HTML islands, QMD cuts) with
  shape-routing: modern PSR/namespaced files keep the generic chunker.
- `chunks` CLI command, `megabrain_chunks` MCP tool, `GET /chunks` endpoint:
  every chunk of one file scored for a query, with selected flags.

## 0.3.1
- PHP `use`-statement import graph (PSR-4-agnostic FQCN index).
- Edge-preservation fix: re-indexing a file no longer destroys incoming edges
  indexed earlier in the same pass; GRAPH_EXTRAS retuned 6 → 7.
- Configurable index excludes: `--exclude` + `.megabrainignore`.

## 0.3.0
- PHP support; PyPI packaging; provider abstraction via OpenRouter
  (`MEGABRAIN_EMBED_MODEL` / `MEGABRAIN_ASK_MODEL` / local OpenAI-compatible
  endpoints).
