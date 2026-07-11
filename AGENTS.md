# megabrain — agent orientation

## What this project is

megabrain is a local **code-intelligence engine**. One call returns all the code related to a question, explained with the real code spliced in. It exists to replace minutes of file-by-file crawling (grep + Read + explore agents) with one grounded answer. Overview: [README.md](README.md).

Pipeline: `index` (cAST chunk → embed via OpenRouter/local (`pplx-embed-v1-0.6b`) → SQLite, incremental by sha256) → `query` (no-LLM retrieval: dense chunk + file-skeleton fusion + graph candidates; CORE full code + RELATED as a map — `--full` for RELATED code bodies) → `ask` (one chat call — Claude via the Agent SDK when installed, else OpenRouter qwen3-coder — narrates and cites `[[k]]`; the engine replaces each citation with verbatim code — the model cannot rewrite code; streamed live to the terminal; **code-only by default**, `--docs` for a docs-only walkthrough, `--with-docs` for code+docs). **ask v2 (`ask_agents.py`)**: BROAD questions auto fan out — a no-LLM classifier reads the bundle shape, a planner (rerank_model) splits it into ≤4 slices, parallel sub-agents (repo map + no-LLM retrieval tools `search_more`/`get_file`/`get_symbol`) explain their slice, a synthesizer merges with the same global `[[k]]` splice; every stage fails open to single-agent ask; CLI `--agents`/`--no-agents`, MCP `agents`, HTTP `POST /ask/stream` (SSE live view). CLI ask/query/chunks auto-refresh a stale index (60s TTL) like the MCP server. Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
ruff check .
python3 -m pytest -q                 # full OFFLINE suite (no key/network) — this is what CI runs
```
For a change under `megabrain/` also run the retrieval gates (local indexed corpus, kept out of
this repo): `python3 tests/test_engine_golden.py` (R@1 ≥ 0.85, **bundle_full ≥ 0.90**),
`python3 tests/test_multi_repo.py`, `python3 tests/test_scale.py`. Current bar: R@1 0.86 ·
bundle_full 1.00 · p50 ~10 ms. Never merge a change that lowers `bundle_full`.

## Contributing workflow — branches, CI, PRs, releases

CI runs on every push and PR (`.github/workflows/ci.yml`): **ruff** lint · the **offline pytest
suite** on a matrix (Python 3.10–3.13 × ubuntu / macOS / **Windows**) · a **build smoke**
(`python -m build` + `megabrain --help`). Keep `master` green; the two local commands above catch
almost everything CI will before you ever push.

**Branches & PRs — don't push non-trivial work straight to `master`:**
```bash
git switch -c fix/thing
#   … commits (small, self-contained; why-focused message; Co-Authored-By trailer) …
git push -u origin fix/thing         # HTTPS is fine for branches with no workflow-file changes
gh pr create --fill                  # CI runs on the PR — merge only when green
```

**Windows is a first-class CI target and has caught real bugs.** Repo-relative paths are POSIX
everywhere: use `Path.as_posix()`, never `str(path)`; match/split on `/`. Don't depend on
case-sensitive filenames or a specific line ending.

**Pushing workflow files:** the default HTTPS OAuth token lacks the `workflow` scope, so a push
that TOUCHES `.github/workflows/*` is rejected. One-off: `git push
git@github.com:bernatch22/megabrain.git <branch>` (SSH); permanent: `gh auth refresh -s workflow`.
Only bites when the workflow YAML itself changes.

**Releases (maintainer only):** bump `megabrain/__init__.py:__version__`, update `CHANGELOG.md`,
tag `vX.Y.Z`, push the tag → `release.yml` builds and publishes to PyPI via Trusted Publishing
(one-time trusted-publisher setup required on pypi.org). **Never `git push` a release tag or
publish to PyPI without explicit approval from the maintainer.**

## Module map

The tree mirrors the pipeline (full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §6): `chunkers/` content→chunks behind one `FileResult` contract (`base` model+partition validator · `python` stdlib-ast cAST · `treesitter` generic chunker + `LangSpec`: TS/JS, Ruby, Go, Rust, PHP · `php` legacy-PHP section chunker + shape router · `markdown` no-LLM QMD doc chunker) · `indexing/` build the index (`indexer` registry-driven incremental walk + `maybe_reindex` 60s TTL · `strategies` ext→registry + `ChunkStrategy` protocol, custom via `index_repo(strategies=[...])` — examples/02 · `graph` py/ts/php edges) · `retrieval/` answer queries, no LLM (`query` fusion + bundle + RELATED-map render + `load_state`/`search_with_state` warm split · `issue` py+js/ts traceback grounding · `bm25` postings lane · `rerank` `llm_order`, `--best`) · root `forge.py` self-authored chunkers (uncovered-ext census → LLM-generate a ChunkStrategy → partition-oracle validate w/ repair loop → trust-gated install to `<repo>/.megabrain/strategies/`, auto-loaded by every index incl. the 60s refresh; CLI `forge`/`trust`, MCP `megabrain_forge`) · `providers/` model APIs (`__init__` chat routing + OpenAI-compat clients · `claude` Agent SDK transport · `embeddings` int8+L2, atomic cache) · `frontends/` entry points (`cli` · `mcp` stdio · `http` serve-api: `/search` `/docsearch` `/chunks` `/ask` `/ask/stream` (SSE) `/get` `/index` `/health`, Bearer `--token`) · root: `ask.py` spliced walkthrough (+ `_Splicer`) · `ask_agents.py` ask v2 (classifier · planner · parallel tool-enabled sub-agents · synthesizer · `stream_events` event driver) · `store.py` SQLite · `mcp_server.py` launcher shim (keeps `python3 -m megabrain.mcp_server` working).

## What's next

**`ask v2` (adaptive multi-agent synthesis) is SHIPPED** — `ask_agents.py`: no-LLM
broad/scoped classifier → planner → ≤4 parallel sub-agents with retrieval tools →
synthesizer with the same global `[[k]]` splice, streamed as events (CLI status lines,
`POST /ask/stream` SSE, the webui agent-card live view). Remaining ask v2 follow-ups in
[NEXT.md](NEXT.md): port the multi-agent view to the bernardocastro.dev demo, and an
eval to tune the classifier thresholds on real broad/scoped query pairs.

**Live demo** (`bernardocastro.dev/megabrain`): the retrieval engine over 7 pre-indexed
public repos, built on `examples/webui/`. Backend + frontend live in the
`bernardocastro.dev` repo (`services/megabrain/` + `src/components/Megabrain.astro`), not
here — see the global `~/.claude/CLAUDE.md` "bernardocastro.dev" section.

**`forge` is SHIPPED** (`forge.py`): `megabrain forge <repo>` detects uncovered text
extensions, LLM-writes a ChunkStrategy per type (the `ask` provider stack), accepts it
only after a clean `validate_partition` over EVERY matching repo file (repair loop),
and installs it trust-gated in `<repo>/.megabrain/strategies/` — loaded by every
index/auto-refresh from then on. Verified on pallets/click (.toml + .yaml,
first-attempt). Tests: `tests/test_forge.py` (offline, fake LLM).

**`forge --specialize` is SHIPPED** (`forge_specialize.py` + `forge_eval.py`): for
COVERED types the built-in chunks poorly (dominant data table / blob / line-window
fallback), parallel LLMs write shape-routers (special shape → tight chunks, everything
else delegates to `builtin_strategy_for` byte-identically). Partition is necessary but
not sufficient here, so installs are gated by a measured retrieval A/B: neutral probe
spans from the file's own structure, span-IoU + hit@k on every changed file, win =
pooled IoU lift ≥0.01 with no per-file regression; a losing candidate gets one
regeneration seeded with the measured result. Validated on psf/requests
status_codes.py: IoU 0.009→0.132 (14×), beat the hand-written reference. Tests:
`tests/test_forge_specialize.py` (offline: fake LLM + FakeEmbedder drive the real gate).

Priority 1 (chunking-strategy registry) is
**done**: a `strategies.py` maps extension → chunk strategy, so the indexer is content-
agnostic. Indexed today: `.py` · `.ts/.tsx/.js/.jsx/.mjs/.cjs` (TS grammar, JS-superset) ·
Ruby `.rb` · Go `.go` · Rust `.rs` · PHP `.php` (optional — `pip install 'megabrain[languages]'`) ·
markdown `.md/.markdown/.mdx` (no-LLM QMD-style scored chunking). Adding a language or
content type is now a registry entry, not a branch in the indexer.

**Packaging done**: published to PyPI (`pip install megabrain`, MIT) — `pyproject.toml`,
console entry point, version single-sourced from `megabrain/__init__.py`. **serve-api done**:
`frontends/http.py` exposes warm-state retrieval over HTTP (serve-api); it powers semantic search on
docs.pinecall.io (a megabrain daemon behind nginx). **Provider abstraction done**: all
LLM/embedding traffic goes through `providers.py` (OpenRouter, OpenAI-compatible) — any model
is selectable by env. Remaining Priority 2: `.tsx` arrow-component symbols, SWE-bench eval.

Provider: chat (ask/--best) routes by `MEGABRAIN_CHAT_PROVIDER` — default AUTO (`claude` when
its SDK is importable, else `openrouter`). `claude` = `providers_claude.py` (Claude Agent SDK:
Claude Code subscription credits or `ANTHROPIC_API_KEY`; default model `haiku`; extra
`megabrain[claude]`). Embeddings NEVER use this switch — always OpenRouter/local (Anthropic has
no embeddings API). Key `OPENROUTER_API_KEY`
(required for embeddings) — env or `~/.zshrc` fallback. Models overridable by env: `MEGABRAIN_EMBED_MODEL`
(default `perplexity/pplx-embed-v1-0.6b`), `MEGABRAIN_ASK_MODEL` / `MEGABRAIN_RERANK_MODEL`
(default `qwen/qwen3-coder` — a code bakeoff found it on par with claude-haiku-4.5 on
citation selection at ~5x lower cost, since retrieval already guarantees completeness). Embeddings AND chat can each target a non-OpenRouter OpenAI-compatible
endpoint via `MEGABRAIN_EMBED_BASE_URL` / `MEGABRAIN_CHAT_BASE_URL` (+ `_API_KEY` variants;
`PERPLEXITY_API_KEY` auto-picked for `api.perplexity.ai`; localhost endpoints — Ollama,
LM Studio, vLLM — need no key; `MEGABRAIN_EMBED_BATCH` shrinks request size for local
servers). Local/hybrid stacks measured in `evals/LOCAL_MODELS.md`. Dims are inferred per model (`MEGABRAIN_EMBED_DIMS` to assert). Changing the
embed model auto-triggers a full re-embed on next `index` (or `index --force`). Repo:
github.com/bernatch22/megabrain.

Embedding bakeoff (2026-07-01, python golden / sdk-server): **pplx-embed-v1-0.6b wins** — R@1
0.864, bundle_full 0.955, ~11ms; no OpenRouter model beats it (pplx-4b, codestral-embed,
openai-3-large, bge-m3 all ≤0.909 bundle_full; gemini-2 ties 0.955 but R@1 collapses to 0.636).
Perplexity-direct == pplx-via-OpenRouter (identical 0.955, same q16 miss) → OpenRouter is a
faithful proxy; the 1.00→0.955 vs the June corpus is embedding-model drift, not the migration.
`evals/embed_bakeoff.py` reproduces it.
