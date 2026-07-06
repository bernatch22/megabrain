# megabrain — Open-Source Refactor Plan

Target: take the engine (which works — R@1 0.86, bundle_full 1.00) to a polished,
contributable open-source Python library **without changing retrieval behavior**.

## Non-negotiable constraints (from AGENTS.md / megabrain-dev skill)

1. No LLM in the retrieval/query path. 2. Never lower golden `bundle_full` (1.00).
3. Graph never ranks. 4. `validate_partition` stays clean. 5. `ask` splices verbatim code only.

**Gate protocol — after EVERY phase:**

```bash
python3 -m pytest -q                                  # full offline suite
python3 tests/test_engine_golden.py                   # local: R@1>=0.85, bundle_full>=0.90
python3 tests/test_multi_repo.py && python3 tests/test_scale.py
```

Phases 1–3 must be **byte-identical** in output (pure moves/renames/deletes).
Anything marked ⚠️ changes behavior and needs an explicit gate re-run + diff review.

---

## Phase 0 — Clean the tree (before anything)

- [ ] Commit the in-flight work first: legacy-PHP chunker (`chunker_php.py`,
      `tests/test_php_legacy_chunker.py`) + the 7 modified files. Refactor starts from a green, clean tree.
- [ ] Verify `python3 -m pytest -q` green as the baseline.

## Phase 1 — Dead code & correctness (small, high value)

- [ ] **Delete `rerank.py` (v1).** `haiku_order` has zero callers (only `rerank2.haiku_order2`
      is used, from `query.py`). Then **rename `rerank2.py` → `rerank.py`** and
      `haiku_order2` → `llm_order` (the model is qwen by default now — "haiku" naming is stale).
      Update the one import in `query.py` and the AGENTS.md module map.
- [ ] **Security: path traversal in `get_code`** (`query.py`). `Path(root)/relpath` accepts
      `../../etc/passwd`; exposed over HTTP by `serve.py /get` (worst with `--host 0.0.0.0`)
      and via MCP `megabrain_get`. Fix: `resolved = (root/rel).resolve(); resolved.relative_to(root.resolve())`
      → error on escape. Add a test.
- [ ] **Optional auth for serve-api**: `--token` / `MEGABRAIN_API_TOKEN` (Bearer check),
      at minimum required for `POST /index` when host != 127.0.0.1. Document.
- [ ] `mcp_server.py` serverInfo hardcodes `"version": "0.1.0"` → use `__version__`.
- [ ] `DEFAULT_BUDGET = 4000` is declared in 4 modules (chunker, chunker_ts, chunker_php,
      markdown) → import from one place (chunker/base).
- [ ] `Chunk.id` (sha1 of span) is vestigial — the DB uses its own autoincrement id and
      never stores it. Remove the field or store it; don't keep both meanings of "id".
- [ ] CLI: `megabrain ask ~/a,~/b` / `get` silently drop everything after the first comma
      (only `query`/`index` handle multi-path). Error out with a clear message (or support it).

## Phase 2 — De-Pinecall the engine ⚠️ (project-specific leaks)

An open-source engine can't carry the maintainer's environment baked in:

- [ ] `indexer.EXCLUDE_DIRS` contains bespoke entries: `src.bkp`, `.brainbank`, `clones`,
      `wt`, `wt_ask`, `wt_best`. Worse, generic-sounding `data` and `logs` will silently skip
      legitimate source dirs in other people's repos. **Trim to universal defaults**
      (`__pycache__ .git node_modules .venv venv dist build coverage .next .pytest_cache .megabrain`)
      and move the bespoke ones to the megabrain repo's own `.megabrainignore`.
      ⚠️ Behavior change on index — re-run gates + re-index check.
- [ ] `serve.py _GROUPS` hardcodes pinecall docs-web sections (`api/ → "SDK API"`, voice-core,
      …). Make it configurable (constructor param + `MEGABRAIN_DOCSEARCH_GROUPS` JSON env, or a
      `docsearch.json` next to the index) with a generic fallback ("Docs").
- [ ] `providers._resolve` reads API keys out of `~/.zshrc`. Surprising for OSS (and
      mac/zsh-only). Keep as a documented convenience fallback, but say so in README; make sure
      it never runs on Windows paths that don't exist (it already guards `exists()`).
- [ ] `strategies.PythonStrategy.build_edge_ctx` seeds `pkg_prefixes` from
      `repo_name.replace("-","_")` + `src/<pkg>` — fine and generic; just document it.

## Phase 3 — Package layout (pure moves; keep entry points stable)

Recommended **moderate** restructure — group the 5 chunking modules, keep the rest flat
(20 small modules don't warrant a deep tree):

```
megabrain/
  __init__.py           # public API (see Phase 4) + __version__
  chunkers/
    __init__.py         # re-exports: Chunk, Symbol, FileResult, validate_partition, embed_text
    base.py             # ← chunker.py data model + validate_partition + nws + embed_text
    python.py           # ← CastChunker
    treesitter.py       # ← chunker_ts.py (TreeSitterChunker + all LangSpecs)
    php.py              # ← chunker_php.py
    markdown.py         # ← markdown.py (MarkdownChunker + qmd_cut)
  strategies.py  store.py  indexer.py  graph.py
  query.py  issue.py  bm25.py  rerank.py  ask.py
  providers.py  embeddings.py
  serve.py  mcp_server.py  cli.py
```

Hard rules for the move:

- [ ] **`python3 -m megabrain.mcp_server` MUST keep working** — it's registered in users'
      `claude mcp add` configs. Same for the `megabrain` console script.
- [ ] Leave one-release deprecation shims for old module paths that tests/evals import
      (`megabrain/chunker.py` → `from .chunkers.base import *; from .chunkers.python import *`).
- [ ] `pyproject`: `packages = ["megabrain"]` → `[tool.setuptools.packages.find]` so the
      subpackage ships.
- [ ] CastChunker currently splits Python's data model and algorithm in one file; keep the
      split base/python exactly along those lines — no logic edits in this phase.

## Phase 4 — Library design patterns (Python-OSS conventions)

- [ ] **Public API**: `megabrain/__init__.py` exports `index_repo`, `search`, `ask`,
      `load_state`, `search_with_state`, `__version__` with `__all__`, via **lazy `__getattr__`**
      so `import megabrain` doesn't pull numpy/tree_sitter. Add `py.typed` marker
      (+ `package-data`) — the codebase is already well-annotated.
- [ ] **Config at call time, not import time.** `providers.py` / `embeddings.py` freeze env
      into module constants on import (`EMBED_MODEL`, `BATCH`, `CACHE`, base URLs) — awkward for
      tests and for embedding megabrain as a lib. Introduce a small `Settings` dataclass
      (`providers.settings()` resolved lazily, overridable per call), keep the module constants
      as thin properties for one release. ⚠️ touch carefully; pure plumbing, gates after.
- [ ] **Strategy Protocol**: define `class ChunkStrategy(typing.Protocol)` (`exts`,
      `chunk_file`, `build_edge_ctx`, `extract_edges`) in `strategies.py`. The registry pattern
      is already right (OCP: new language = config entry) — formalize the contract for
      contributors.
- [ ] **`logging` instead of `print`** in `indexer.py` / `serve.py` (library code must not
      print). CLI configures the handler; `MEGABRAIN_DEBUG=1` sets DEBUG level. In `ask.py`, the
      bare `except Exception: text = ""` silently eats provider errors — log them at debug so
      failures are diagnosable.
- [ ] **`Store` lifecycle**: add `close()` + context-manager support. `ask()` builds a second
      `Store` after `search()` already opened one — pass/reuse state instead
      (also lets `serve.py /ask` use the **warm state** instead of reloading matrices per request,
      which its own docstring apologizes for).

## Phase 5 — Algorithm review: keep / improve

**Keep as-is (validated by experiment, do not touch):** cAST split-then-merge + partition
guarantee; dense + 0.5×skeleton fusion; graph as candidates-only; RRF (k=60) issue ensemble;
no-LLM path; rerank as permute-only with bounded demotion (MAX_FALL=1) — all sound and locked.

Improvements that respect the locked rules (each ⚠️ = gates after):

- [ ] **BM25 efficiency** (`bm25.py`): `scores()` loops all N docs per term. Build a postings
      map `term → [(doc, tf)]` in `__init__` and iterate only matching docs. Also: in issue mode
      `query.py` rebuilds BM25 + the full symbol-doc corpus **per query** — cache it on
      `SearchState` (matters for serve-api).
- [ ] ⚠️ **Test-file heuristic** (`query.py`): `"test" in <dir-or-name>` matches `latest/`,
      `contest.py`. Tighten to word-boundary (`test`/`tests`/`*_test`/`test_*`). Gate re-run
      mandatory (affects ranking).
- [ ] **Embedding cache**: one tiny `.npy` per chunk under `~/.megabrain/cache` → thousands of
      files, never pruned. Move to a single SQLite (or `.npz` shard) keyed by sha; add a
      `megabrain cache prune` or size cap. Optional: store int8 vectors in the repo DB
      (4× smaller, dot-product unchanged after dequant) — measure first.
- [ ] **`issue.py` paths**: `_PYPATH` only grounds `.py` paths — extend to
      `.ts/.tsx/.js/.go/.rb/.rs/.php` so non-Python tracebacks/issues ground files too.
- [ ] **TS graph** (`graph.py`): handle `export * from '...'`, dynamic `import('...')`,
      `.mts/.cts`; (optional, later) tsconfig path aliases.
- [ ] `search_multi`: per-repo searches run serially — trivial `ThreadPoolExecutor` win.
- [ ] Brute-force cosine is fine to ~50K chunks (documented). Leave HNSW deferred; state the
      threshold in ARCHITECTURE/README so nobody "optimizes" it prematurely.

## Phase 6 — OSS scaffolding & packaging

- [ ] **CI (GitHub Actions)**: offline pytest matrix — py 3.10–3.13 × (ubuntu, macos, windows)
      (the suite is already network-free); `ruff check`; `python -m build`. Badge in README.
- [ ] **ruff** config in pyproject (lint + format or keep style, just lint). Optional pre-commit.
- [ ] **Release flow**: tag-triggered publish with PyPI Trusted Publishing; `CHANGELOG.md`
      (backfill 0.3.x from git log).
- [ ] **Community files**: `CONTRIBUTING.md` (dev setup, gate protocol, "adding a language =
      LangSpec entry" as the golden first-issue path), `SECURITY.md`. Note that the golden eval
      set is private — explain how contributors validate (offline suite + partition gates).
- [ ] **pyproject**: add `3.10`/`3.13` classifiers + `Development Status :: 4 - Beta`,
      `project.urls` Issues/Changelog; fix the stale comment ("urllib handles the
      Perplexity/Anthropic calls" → OpenRouter/OpenAI-compatible).
- [ ] Nice-to-have features: `megabrain ask --json`; new `LangSpec`s (Java, C#, Kotlin, C/C++)
      as labeled good-first-issues; Windows path audit (`~/.zshrc` fallback is a no-op there — fine).

## Phase 7 — Docs sync (a task isn't done while the docs lie)

README is **good but stale in specific spots** — fix all of these:

1. MCP section: tool list is missing **`megabrain_chunks`**; HTTP table is missing **`GET /chunks`**.
2. Usage: missing **`megabrain chunks`** and **`megabrain stats`** subcommands, `--best`
   (rerank), `query --json`.
3. Languages section: "graph edges are built for Python and TS/JS today" — **stale: the PHP
   `use`-import graph shipped** (and the legacy-PHP section chunker is a README-worthy feature).
4. Design/limits: document serve-api auth (new `--token`), the docsearch group config, and the
   trimmed default excludes.
5. AGENTS.md module map + megabrain-dev skill: update paths after Phase 3 (chunkers/ package,
   rerank rename).
6. `evals/README.md`, ARCHITECTURE.md (local): sync module names.

---

## Suggested execution order & risk

| order | phase | risk | gate |
|---|---|---|---|
| 1 | 0 commit WIP | none | pytest |
| 2 | 1 dead code + traversal fix | low | pytest |
| 3 | 2 de-pinecall | ⚠️ medium (index behavior) | full gates + re-index diff |
| 4 | 3 layout moves | low (mechanical) | pytest byte-diff on chunker outputs |
| 5 | 4 API/config/logging | medium | full gates |
| 6 | 5 algorithm tweaks | ⚠️ per-item | full gates per item |
| 7 | 6 scaffolding | none | CI green |
| 8 | 7 docs sync | none | grep for stale claims |

## Decision points for the maintainer (don't let the executor guess)

1. **Layout depth**: moderate (`chunkers/` only — recommended) vs full (`retrieval/`, `servers/` too)?
2. **`EXCLUDE_DIRS` trim**: OK to stop excluding `data`/`logs` by default? (Recommended: yes, breaking-ish but correct for OSS.)
3. **`~/.zshrc` key fallback**: keep-and-document (recommended) or drop?
4. **rerank rename** (`rerank2→rerank`, `haiku_order2→llm_order`): approve the public-name change?
