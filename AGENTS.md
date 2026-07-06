# megabrain — agent orientation

## What this project is

megabrain is a local **code-intelligence engine**. One call returns all the code related to a question, explained with the real code spliced in. It exists to replace minutes of file-by-file crawling (grep + Read + explore agents) with one grounded answer. Overview: [README.md](README.md).

Pipeline: `index` (cAST chunk → OpenRouter embed (`pplx-embed-v1-0.6b`) → SQLite, incremental by sha256) → `query` (no-LLM retrieval: dense chunk + file-skeleton fusion + graph candidates) → `ask` (one OpenRouter chat call, qwen3-coder by default, narrates and cites `[[k]]`; the engine replaces each citation with verbatim code — the model cannot rewrite code; streamed live to the terminal; **code-only by default**, `--docs` for a docs-only walkthrough).

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

`chunkers/` all chunking behind one `FileResult` contract — `base.py` data model + partition validator · `python.py` stdlib-ast cAST · `treesitter.py` generic `TreeSitterChunker` + `LangSpec` (TS/JS, Ruby, Go, Rust, PHP) · `php.py` legacy-PHP section chunker + shape router · `markdown.py` no-LLM QMD-style doc chunker (old top-level module names remain as deprecation shims) · `strategies.py` ext→strategy registry + `ChunkStrategy` protocol (custom strategies inject via `index_repo(strategies=[...])`, checked before built-ins — examples/02) · `providers.py` OpenRouter config + shared OpenAI-compat chat/embed clients · `embeddings.py` pplx (int8, L2-norm) via OpenRouter · `store.py` SQLite · `graph.py` import/call edges (Python, TS/JS, PHP) · `indexer.py` registry-driven incremental walk (built-in excludes + `.megabrainignore`/`--exclude`) · `query.py` fusion + bundle + render (split into `load_state` / `search_with_state` so a server can keep the matrix warm) · `issue.py` deterministic issue parsing (Python + JS/TS traceback grounding, variant ensemble) · `bm25.py` sparse entity lane (postings) · `rerank.py` optional listwise LLM reorder (`llm_order`, `--best`) · `ask.py` explanation with spliced code · `serve.py` warm-state HTTP API (`serve-api`: `/search` `/docsearch` `/chunks` `/ask` `/get` `/index` `/health`; optional Bearer `--token`) · `cli.py` · `mcp_server.py`.

## What's next

Priority 1 (chunking-strategy registry) is
**done**: a `strategies.py` maps extension → chunk strategy, so the indexer is content-
agnostic. Indexed today: `.py` · `.ts/.tsx/.js/.jsx/.mjs/.cjs` (TS grammar, JS-superset) ·
Ruby `.rb` · Go `.go` · Rust `.rs` · PHP `.php` (optional — `pip install 'megabrain[languages]'`) ·
markdown `.md/.markdown/.mdx` (no-LLM QMD-style scored chunking). Adding a language or
content type is now a registry entry, not a branch in the indexer.

**Packaging done**: published to PyPI (`pip install megabrain`, MIT) — `pyproject.toml`,
console entry point, version single-sourced from `megabrain/__init__.py`. **serve-api done**:
`serve.py` exposes warm-state retrieval over HTTP; it powers semantic search on
docs.pinecall.io (a megabrain daemon behind nginx). **Provider abstraction done**: all
LLM/embedding traffic goes through `providers.py` (OpenRouter, OpenAI-compatible) — any model
is selectable by env. Remaining Priority 2: `.tsx` arrow-component symbols, SWE-bench eval.

Provider: everything runs through **OpenRouter** (`providers.py`). Key `OPENROUTER_API_KEY`
(required) — env or `~/.zshrc` fallback. Models overridable by env: `MEGABRAIN_EMBED_MODEL`
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
